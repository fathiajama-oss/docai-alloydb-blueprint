# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""FastAPI backend serving the Document AI to AlloyDB blueprint application."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import SAMPLE_DOCS_DIR, STATIC_DIR, get_settings
from app.db import (
    DatabaseServiceError,
    check_db_health,
    delete_license_record,
    init_db,
    insert_license_record,
    load_schema_ddl,
    query_license_records,
)
from app.docai_service import (
    PipelineConfigError,
    PipelineServiceError,
    check_docai_health,
    check_gcs_health,
    process_document_pipeline,
)


ALLOWED_UPLOAD_MIME_TYPES: dict[str, str] = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "application/pdf": "application/pdf",
}

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

SAMPLE_METADATA: dict[str, dict[str, str]] = {
    "sample_dl_california_valid.png": {
        "title": "California — Elena Rivers",
        "state": "California",
        "holder_name": "Elena Marie Rivers",
        "dl_number": "D8492017",
        "expected_status": "VALID",
        "expiration_label": "EXP 08/24/2029",
    },
    "sample_dl_texas_expiring.png": {
        "title": "Texas — Marcus Chen",
        "state": "Texas",
        "holder_name": "Marcus Wei Chen",
        "dl_number": "39104852",
        "expected_status": "EXPIRING_SOON",
        "expiration_label": "EXP 01/15/2027",
    },
    "sample_dl_newyork_expired.png": {
        "title": "New York — Priya Patel",
        "state": "New York",
        "holder_name": "Priya Anjali Patel",
        "dl_number": "719304826",
        "expected_status": "EXPIRED",
        "expiration_label": "EXP 04/02/2025",
    },
}


app = FastAPI(
    title="From Documents to Data: Document AI & AlloyDB Blueprint",
    description="Open-source reference implementation converting US Driver's Licenses via Document AI into structured records in AlloyDB for PostgreSQL.",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _safe_sample_path(filename: str) -> Path:
    """Resolves a sample filename inside SAMPLE_DOCS_DIR while blocking traversal."""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid sample filename.")
    root = SAMPLE_DOCS_DIR.resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Sample document '{filename}' not found.")
    return candidate


def _run_end_to_end_processing(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
) -> dict[str, Any]:
    """Runs the live Cloud Storage -> Document AI -> AlloyDB pipeline."""
    settings = get_settings()
    try:
        pipeline_result = process_document_pipeline(
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            settings=settings,
        )
    except PipelineConfigError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "gcp_unconfigured",
                "stage": "config",
                "message": str(exc),
            },
        ) from exc
    except PipelineServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "gcp_pipeline_failed",
                "stage": exc.stage,
                "message": exc.message,
            },
        ) from exc

    t_db = time.perf_counter()
    try:
        db_record, sql_proof = insert_license_record(settings, pipeline_result["record"])
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "alloydb_write_failed",
                "stage": "alloydb_persist",
                "message": str(exc),
                "partial_pipeline": {
                    "source_gcs_uri": pipeline_result["source_gcs_uri"],
                    "output_gcs_uri": pipeline_result["output_gcs_uri"],
                    "timings": pipeline_result["timings"],
                    "entities_with_boxes": pipeline_result["entities_with_boxes"],
                    "raw_docai_json": pipeline_result["raw_docai_json"],
                },
            },
        ) from exc
    alloydb_write_ms = round((time.perf_counter() - t_db) * 1000, 1)

    timings = dict(pipeline_result["timings"])
    timings["alloydb_write_ms"] = alloydb_write_ms
    timings["total_ms"] = round(sum(timings.values()), 1)

    return {
        "status": "ok",
        "filename": filename,
        "mime_type": mime_type,
        "timings": timings,
        "source_gcs_uri": pipeline_result["source_gcs_uri"],
        "output_gcs_uri": pipeline_result["output_gcs_uri"],
        "record": db_record,
        "bounding_boxes": pipeline_result["bounding_boxes"],
        "entities_with_boxes": pipeline_result["entities_with_boxes"],
        "sql_proof": sql_proof,
        "ddl_sql": load_schema_ddl(),
        "raw_docai_json": pipeline_result["raw_docai_json"],
    }


@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    """Serves the minimalist web UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    """Checks live Cloud Storage, Document AI, and AlloyDB connectivity."""
    settings = get_settings()
    gcs_status = check_gcs_health(settings)
    docai_status = check_docai_health(settings)
    db_status = check_db_health(settings)

    auto_initialized = False
    if db_status.get("status") == "ok" and not db_status.get("schema_initialized"):
        try:
            init_db(settings)
            db_status = check_db_health(settings)
            auto_initialized = True
        except DatabaseServiceError as exc:
            db_status = {
                "status": "error",
                "schema_initialized": False,
                "detail": f"Connected to AlloyDB, but auto-schema init failed: {exc}",
            }

    ready = all(
        comp.get("status") == "ok" for comp in (gcs_status, docai_status, db_status)
    )

    return {
        "ready": ready,
        "schema_auto_initialized": auto_initialized,
        "ddl_sql": load_schema_ddl(),
        "components": {
            "cloud_storage": gcs_status,
            "document_ai": docai_status,
            "alloydb": db_status,
        },
    }


@app.get("/api/samples")
def api_list_samples() -> dict[str, Any]:
    """Lists synthetic sample US Driver's Licenses available in sample_docs/."""
    samples: list[dict[str, Any]] = []
    if SAMPLE_DOCS_DIR.is_dir():
        for png_path in sorted(SAMPLE_DOCS_DIR.glob("*.png")):
            pdf_candidate = png_path.with_suffix(".pdf")
            meta = SAMPLE_METADATA.get(png_path.name, {})
            samples.append(
                {
                    "filename": png_path.name,
                    "pdf_filename": pdf_candidate.name if pdf_candidate.is_file() else None,
                    "title": meta.get("title", png_path.stem.replace("_", " ").title()),
                    "state": meta.get("state", "US Specimen"),
                    "holder_name": meta.get("holder_name", "Synthetic Specimen"),
                    "dl_number": meta.get("dl_number", ""),
                    "expected_status": meta.get("expected_status", "SPECIMEN"),
                    "expiration_label": meta.get("expiration_label", ""),
                    "preview_url": f"/api/samples/{png_path.name}",
                    "pdf_url": (
                        f"/api/samples/{pdf_candidate.name}"
                        if pdf_candidate.is_file()
                        else None
                    ),
                }
            )
    return {"samples": samples}


@app.get("/api/samples/{filename}")
def api_get_sample_file(filename: str) -> FileResponse:
    """Serves a sample PNG or PDF safely from sample_docs/."""
    path = _safe_sample_path(filename)
    suffix = path.suffix.lower()
    if suffix == ".png":
        media_type = "image/png"
    elif suffix == ".pdf":
        media_type = "application/pdf"
    else:
        raise HTTPException(status_code=400, detail="Unsupported sample file type.")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/api/process/upload")
async def api_process_upload(file: UploadFile = File(...)) -> JSONResponse:
    """Uploads a user-provided image or PDF and runs the live DocAI -> AlloyDB pipeline."""
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_UPLOAD_MIME_TYPES:
        ext = Path(file.filename or "").suffix.lower()
        ext_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf"}
        if ext in ext_map:
            content_type = ext_map[ext]
        else:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unsupported_media_type",
                    "message": "Please upload a PNG, JPEG, or PDF document.",
                },
            )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=400,
            detail={"error": "empty_file", "message": "Uploaded file is empty."},
        )
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": "file_too_large", "message": "File exceeds 20 MB limit."},
        )

    result = _run_end_to_end_processing(
        file_bytes=file_bytes,
        filename=file.filename or "uploaded_license",
        mime_type=ALLOWED_UPLOAD_MIME_TYPES[content_type],
    )
    return JSONResponse(content=result)


@app.post("/api/process/sample/{filename}")
def api_process_sample(filename: str) -> JSONResponse:
    """Processes one of the synthetic sample files through the live pipeline."""
    path = _safe_sample_path(filename)
    suffix = path.suffix.lower()
    mime_map = {".png": "image/png", ".pdf": "application/pdf"}
    mime_type = mime_map.get(suffix)
    if not mime_type:
        raise HTTPException(status_code=400, detail="Unsupported sample format.")

    result = _run_end_to_end_processing(
        file_bytes=path.read_bytes(),
        filename=path.name,
        mime_type=mime_type,
    )
    return JSONResponse(content=result)


@app.get("/api/records")
def api_list_records(
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    """Queries processed license records and summary counts directly from AlloyDB."""
    settings = get_settings()
    try:
        rows, summary, sql_proof = query_license_records(
            settings=settings,
            status_filter=status,
            search=search,
            limit=limit,
        )
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "alloydb_unavailable",
                "message": str(exc),
            },
        ) from exc

    return JSONResponse(
        content={
            "records": rows,
            "summary": summary,
            "sql_proof": sql_proof,
            "ddl_sql": load_schema_ddl(),
        }
    )


@app.post("/api/schema/init")
def api_init_schema() -> JSONResponse:
    """Explicitly executes sql/01_init_schema.sql against AlloyDB."""
    settings = get_settings()
    try:
        ddl = init_db(settings)
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "alloydb_init_failed", "message": str(exc)},
        ) from exc
    return JSONResponse(content={"status": "ok", "ddl": ddl})


@app.delete("/api/records/{record_id}")
def api_delete_record(record_id: int) -> JSONResponse:
    """Deletes a persisted driver's license row by ID."""
    settings = get_settings()
    try:
        deleted = delete_license_record(settings, record_id)
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "alloydb_delete_failed", "message": str(exc)},
        ) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found.")
    return JSONResponse(content={"status": "deleted", "id": record_id})
