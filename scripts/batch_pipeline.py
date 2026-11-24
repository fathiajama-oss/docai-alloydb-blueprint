# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Standalone CLI batch & online pipeline matching the Medium blueprint.

Uploads driver's license documents to Cloud Storage, runs Document AI
(online `process_document` or asynchronous `batch_process_documents`),
cleans extracted fields, and persists records into AlloyDB for PostgreSQL.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
import sys
from typing import Iterable

# Ensure project root is importable when invoked as `python scripts/batch_pipeline.py`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.cleaning import extract_driver_license_record  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db, insert_license_record  # noqa: E402
from app.docai_service import (  # noqa: E402
    batch_process_gcs_documents,
    process_document_pipeline,
    upload_bytes_to_gcs,
)


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}


def _discover_documents(input_dir: Path, include_duplicate_pdfs: bool = False) -> list[Path]:
    """Discovers supported image/PDF documents in `input_dir`."""
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    all_files = sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if include_duplicate_pdfs:
        return all_files

    # Avoid double-processing companion PDFs when a PNG with the same stem exists
    png_stems = {p.stem for p in all_files if p.suffix.lower() in {".png", ".jpg", ".jpeg"}}
    filtered: list[Path] = []
    for path in all_files:
        if path.suffix.lower() == ".pdf" and path.stem in png_stems:
            continue
        filtered.append(path)
    return filtered


def _infer_mime_type(path: Path) -> str:
    """Infers MIME type for PNG, JPEG, or PDF files."""
    ext = path.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".pdf":
        return "application/pdf"
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def run_online(files: Iterable[Path]) -> int:
    """Processes each document synchronously via Document AI and inserts into AlloyDB."""
    settings = get_settings()
    init_db(settings)

    processed = 0
    for path in files:
        mime_type = _infer_mime_type(path)
        print(f"[online] Processing {path.name} ({mime_type})...")
        result = process_document_pipeline(
            file_bytes=path.read_bytes(),
            filename=path.name,
            mime_type=mime_type,
            settings=settings,
        )
        inserted, _ = insert_license_record(settings, result["record"])
        processed += 1
        print(
            f"  -> Persisted id={inserted['id']} "
            f"dl={inserted.get('dl_number')} "
            f"name={inserted.get('given_name')} {inserted.get('surname')} "
            f"status={inserted.get('compliance_status')} "
            f"source={result['source_gcs_uri']}"
        )
    return processed


def run_batch(files: list[Path], timeout: int) -> int:
    """Uploads documents to Cloud Storage, runs BatchProcessDocuments, and persists."""
    settings = get_settings()
    missing = settings.missing_gcp_fields()
    if missing:
        raise RuntimeError(f"Missing Google Cloud settings in .env: {', '.join(missing)}")

    init_db(settings)

    utc_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    input_prefix = f"batch/{utc_ts}/input/"
    output_prefix = f"batch/{utc_ts}/output/"

    uploaded_uris: list[str] = []
    for path in files:
        blob_name = f"{input_prefix}{path.name}"
        uri = upload_bytes_to_gcs(
            bucket_name=settings.GCS_INPUT_BUCKET,
            blob_name=blob_name,
            data=path.read_bytes(),
            content_type=_infer_mime_type(path),
            project_id=settings.GCP_PROJECT_ID,
        )
        uploaded_uris.append(uri)
        print(f"[batch] Uploaded {path.name} -> {uri}")

    gcs_input_prefix_uri = f"gs://{settings.GCS_INPUT_BUCKET}/{input_prefix}"
    gcs_output_uri = f"gs://{settings.GCS_OUTPUT_BUCKET}/{output_prefix}"

    print(f"[batch] Triggering Document AI batch_process_documents on {gcs_input_prefix_uri}...")
    outputs = batch_process_gcs_documents(
        gcs_input_prefix_uri=gcs_input_prefix_uri,
        gcs_output_uri=gcs_output_uri,
        settings=settings,
        timeout=timeout,
    )

    processed = 0
    for output_uri, doc_dict in outputs:
        extracted = extract_driver_license_record(doc_dict)
        record_payload = {
            "dl_number": extracted["dl_number"],
            "given_name": extracted["given_name"],
            "surname": extracted["surname"],
            "address": extracted["address"],
            "dob": extracted["dob"],
            "expiration_date": extracted["expiration_date"],
            "issue_date": extracted["issue_date"],
            "avg_confidence": extracted["avg_confidence"],
            "raw_entities": extracted["raw_entities"],
            "source_gcs_uri": gcs_input_prefix_uri,
            "output_gcs_uri": output_uri,
        }
        inserted, _ = insert_license_record(settings, record_payload)
        processed += 1
        print(
            f"  -> Persisted id={inserted['id']} "
            f"dl={inserted.get('dl_number')} "
            f"output={output_uri}"
        )
    return processed


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Process driver's license documents with Document AI and store in AlloyDB."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "sample_docs",
        help="Directory containing PNG, JPEG, or PDF documents (default: sample_docs).",
    )
    parser.add_argument(
        "--mode",
        choices=("online", "batch"),
        default="online",
        help="Use synchronous online processing or asynchronous GCS batch processing.",
    )
    parser.add_argument(
        "--include-duplicate-pdfs",
        action="store_true",
        help="Also process PDF companions when an image with the same stem exists.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for Document AI batch operations (default: 300).",
    )
    args = parser.parse_args()

    files = _discover_documents(args.input_dir, include_duplicate_pdfs=args.include_duplicate_pdfs)
    if not files:
        print(f"No supported documents found in {args.input_dir}", file=sys.stderr)
        return 1

    if args.mode == "online":
        count = run_online(files)
    else:
        count = run_batch(files, timeout=args.timeout)

    print(f"Completed processing {count} document(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
