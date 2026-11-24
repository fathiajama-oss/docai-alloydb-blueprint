# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Real Google Cloud Storage and Document AI Layout Parser integration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPIError, InvalidArgument
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import documentai_v1 as documentai
from google.cloud import storage

from app.chunking import extract_chunks_from_docai
from app.config import Settings


class PipelineConfigError(RuntimeError):
    """Raised when required Google Cloud configuration is missing."""


class PipelineServiceError(RuntimeError):
    """Raised when a live Cloud Storage or Document AI call fails."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message


def _sanitize_filename(filename: str) -> str:
    """Sanitizes a filename for safe Cloud Storage object naming."""
    base = Path(filename).name or "document.pdf"
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)


def _docai_client(location: str) -> documentai.DocumentProcessorServiceClient:
    """Creates a Document AI client pinned to the configured regional endpoint."""
    options = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(client_options=options)


def _layout_process_options(chunk_size: int = 450) -> documentai.ProcessOptions:
    """Builds LayoutConfig ChunkingConfig options for Document AI Layout Parser."""
    return documentai.ProcessOptions(
        layout_config=documentai.ProcessOptions.LayoutConfig(
            chunking_config=documentai.ProcessOptions.LayoutConfig.ChunkingConfig(
                chunk_size=chunk_size,
                include_ancestor_headings=True,
            )
        )
    )


def upload_bytes_to_gcs(
    bucket_name: str,
    blob_name: str,
    data: bytes,
    content_type: str,
    project_id: str | None = None,
) -> str:
    """Uploads bytes to Cloud Storage and returns the gs:// URI."""
    try:
        client = storage.Client(project=project_id or None)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{bucket_name}/{blob_name}"
    except (DefaultCredentialsError, GoogleAPIError, Exception) as exc:
        raise PipelineServiceError("gcs_upload", f"Cloud Storage upload failed: {exc}") from exc


def download_json_from_gcs(gcs_uri: str, project_id: str | None = None) -> dict[str, Any]:
    """Downloads and parses a JSON document from a gs:// URI."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got: {gcs_uri}")
    without_scheme = gcs_uri[5:]
    bucket_name, _, blob_name = without_scheme.partition("/")
    try:
        client = storage.Client(project=project_id or None)
        blob = client.bucket(bucket_name).blob(blob_name)
        payload = blob.download_as_text(encoding="utf-8")
        return json.loads(payload)
    except (DefaultCredentialsError, GoogleAPIError, json.JSONDecodeError, Exception) as exc:
        raise PipelineServiceError("gcs_download", f"Cloud Storage JSON download failed: {exc}") from exc


def check_gcs_health(settings: Settings) -> dict[str, Any]:
    """Checks Cloud Storage configuration and live bucket reachability."""
    if not settings.is_gcs_configured():
        return {
            "status": "unconfigured",
            "detail": "Set GCP_PROJECT_ID, GCS_INPUT_BUCKET, and GCS_OUTPUT_BUCKET in .env.",
        }
    try:
        client = storage.Client(project=settings.GCP_PROJECT_ID)
        in_bucket = client.lookup_bucket(settings.GCS_INPUT_BUCKET)
        out_bucket = client.lookup_bucket(settings.GCS_OUTPUT_BUCKET)
        missing = []
        if in_bucket is None:
            missing.append(settings.GCS_INPUT_BUCKET)
        if out_bucket is None:
            missing.append(settings.GCS_OUTPUT_BUCKET)
        if missing:
            return {
                "status": "error",
                "detail": f"Bucket(s) not found or inaccessible: {', '.join(missing)}",
            }
        return {
            "status": "ok",
            "input_bucket": settings.GCS_INPUT_BUCKET,
            "output_bucket": settings.GCS_OUTPUT_BUCKET,
            "detail": "Input and output buckets verified.",
        }
    except (DefaultCredentialsError, GoogleAPIError, Exception) as exc:
        return {"status": "error", "detail": str(exc)}


def check_docai_health(settings: Settings) -> dict[str, Any]:
    """Checks Document AI processor configuration and live API reachability."""
    if not settings.is_docai_configured():
        return {
            "status": "unconfigured",
            "detail": "Set GCP_PROJECT_ID, DOCAI_LOCATION, and DOCAI_PROCESSOR_ID in .env.",
        }
    processor_name = (
        f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.DOCAI_LOCATION}"
        f"/processors/{settings.DOCAI_PROCESSOR_ID}"
    )
    try:
        client = _docai_client(settings.DOCAI_LOCATION)
        processor = client.get_processor(name=processor_name)
        return {
            "status": "ok",
            "processor_name": processor.name,
            "processor_type": processor.type_,
            "display_name": processor.display_name,
            "detail": f"Connected to {processor.type_} ({processor.state.name}).",
        }
    except (DefaultCredentialsError, GoogleAPIError, Exception) as exc:
        return {"status": "error", "detail": str(exc)}


def process_document_pipeline(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    settings: Settings,
) -> dict[str, Any]:
    """Executes Cloud Storage -> Document AI Layout Parser -> Chunk Extraction."""
    missing = settings.missing_gcp_fields()
    if missing:
        raise PipelineConfigError(
            f"Missing Google Cloud configuration in .env: {', '.join(missing)}"
        )

    safe_name = _sanitize_filename(filename)
    stem = Path(safe_name).stem
    utc_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Stage 1: Upload raw PDF to Cloud Storage input bucket
    t0 = time.perf_counter()
    input_blob = f"raw/{utc_ts}_{safe_name}"
    source_gcs_uri = upload_bytes_to_gcs(
        bucket_name=settings.GCS_INPUT_BUCKET,
        blob_name=input_blob,
        data=file_bytes,
        content_type=mime_type,
        project_id=settings.GCP_PROJECT_ID,
    )
    gcs_upload_ms = round((time.perf_counter() - t0) * 1000, 1)

    # Stage 2: Call Document AI Layout Parser with ChunkingConfig
    t1 = time.perf_counter()
    processor_name = (
        f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.DOCAI_LOCATION}"
        f"/processors/{settings.DOCAI_PROCESSOR_ID}"
    )
    try:
        client = _docai_client(settings.DOCAI_LOCATION)
        raw_doc = documentai.RawDocument(content=file_bytes, mime_type=mime_type)
        request = documentai.ProcessRequest(
            name=processor_name,
            raw_document=raw_doc,
            process_options=_layout_process_options(),
        )
        try:
            result = client.process_document(request=request)
        except InvalidArgument as exc:
            # Retry without layout_config if pointed at an OCR/Form processor
            if "layout_config" in str(exc).lower() or "chunking_config" in str(exc).lower() or "process_options" in str(exc).lower():
                fallback_req = documentai.ProcessRequest(
                    name=processor_name,
                    raw_document=raw_doc,
                )
                result = client.process_document(request=fallback_req)
            else:
                raise
    except (DefaultCredentialsError, GoogleAPIError, Exception) as exc:
        raise PipelineServiceError("docai_parse", f"Document AI processing failed: {exc}") from exc
    docai_parse_ms = round((time.perf_counter() - t1) * 1000, 1)

    # Stage 3: Serialize Document AI response and persist JSON to Cloud Storage output bucket
    t2 = time.perf_counter()
    raw_json_str = documentai.Document.to_json(result.document)
    output_blob = f"processed/{utc_ts}_{stem}.json"
    output_gcs_uri = upload_bytes_to_gcs(
        bucket_name=settings.GCS_OUTPUT_BUCKET,
        blob_name=output_blob,
        data=raw_json_str.encode("utf-8"),
        content_type="application/json",
        project_id=settings.GCP_PROJECT_ID,
    )
    gcs_json_write_ms = round((time.perf_counter() - t2) * 1000, 1)

    document_dict = json.loads(raw_json_str)
    page_count, chunks = extract_chunks_from_docai(document_dict)

    if not chunks:
        raise PipelineServiceError(
            "docai_parse",
            "Document AI returned no extractable layout chunks or text for this document.",
        )

    raw_chunks = (
        (document_dict.get("chunkedDocument") or document_dict.get("chunked_document") or {}).get("chunks")
        or []
    )
    docai_json_summary = {
        "mimeType": document_dict.get("mimeType") or mime_type,
        "processor": processor_name,
        "pageCount": page_count,
        "chunkCount": len(chunks),
        "sampleChunks": raw_chunks[:5] if raw_chunks else chunks[:5],
    }

    return {
        "timings": {
            "gcs_upload_ms": gcs_upload_ms,
            "docai_parse_ms": docai_parse_ms,
            "gcs_json_write_ms": gcs_json_write_ms,
        },
        "source_gcs_uri": source_gcs_uri,
        "output_gcs_uri": output_gcs_uri,
        "page_count": page_count,
        "chunks": chunks,
        "raw_docai_json": docai_json_summary,
    }


def batch_process_gcs_documents(
    gcs_input_prefix_uri: str,
    gcs_output_uri: str,
    settings: Settings,
    timeout: int = 300,
) -> list[tuple[str, dict[str, Any]]]:
    """Runs asynchronous Document AI batch processing on a Cloud Storage prefix.

    Returns a list of `(output_gcs_uri, parsed_document_dict)` tuples.
    """
    missing = settings.missing_gcp_fields()
    if missing:
        raise PipelineConfigError(
            f"Missing Google Cloud configuration in .env: {', '.join(missing)}"
        )

    processor_name = (
        f"projects/{settings.GCP_PROJECT_ID}/locations/{settings.DOCAI_LOCATION}"
        f"/processors/{settings.DOCAI_PROCESSOR_ID}"
    )

    if not gcs_output_uri.endswith("/"):
        gcs_output_uri = f"{gcs_output_uri}/"

    try:
        client = _docai_client(settings.DOCAI_LOCATION)
        input_cfg = documentai.BatchDocumentsInputConfig(
            gcs_prefix=documentai.GcsPrefix(gcs_uri_prefix=gcs_input_prefix_uri)
        )
        output_cfg = documentai.DocumentOutputConfig(
            gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                gcs_uri=gcs_output_uri
            )
        )
        request = documentai.BatchProcessRequest(
            name=processor_name,
            input_documents=input_cfg,
            document_output_config=output_cfg,
            process_options=_layout_process_options(),
        )
        try:
            operation = client.batch_process_documents(request=request)
            operation.result(timeout=timeout)
        except InvalidArgument as exc:
            if "layout_config" in str(exc).lower() or "chunking_config" in str(exc).lower() or "process_options" in str(exc).lower():
                fallback_req = documentai.BatchProcessRequest(
                    name=processor_name,
                    input_documents=input_cfg,
                    document_output_config=output_cfg,
                )
                operation = client.batch_process_documents(request=fallback_req)
                operation.result(timeout=timeout)
            else:
                raise
    except (DefaultCredentialsError, GoogleAPIError, Exception) as exc:
        raise PipelineServiceError("docai_batch", f"Batch processing failed: {exc}") from exc

    without_scheme = gcs_output_uri[5:]
    bucket_name, _, prefix = without_scheme.partition("/")
    storage_client = storage.Client(project=settings.GCP_PROJECT_ID)
    blobs = storage_client.list_blobs(bucket_name, prefix=prefix)

    outputs: list[tuple[str, dict[str, Any]]] = []
    for blob in blobs:
        if not blob.name.endswith(".json"):
            continue
        uri = f"gs://{bucket_name}/{blob.name}"
        payload = json.loads(blob.download_as_text(encoding="utf-8"))
        outputs.append((uri, payload))
    return outputs
