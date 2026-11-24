# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""FastAPI backend for Document AI Layout Parser, AlloyDB pgvector, and Gemini Chat."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import SAMPLE_DOCS_DIR, STATIC_DIR, get_settings
from app.db import (
    DatabaseServiceError,
    check_db_health,
    delete_document,
    get_document_chunks,
    hybrid_search_chunks,
    init_db,
    insert_document_and_chunks,
    list_documents,
    load_schema_ddl,
)
from app.docai_service import (
    PipelineConfigError,
    PipelineServiceError,
    check_docai_health,
    check_gcs_health,
    process_document_pipeline,
)
from app.rag_service import (
    RagServiceError,
    check_vertex_health,
    embed_texts,
    generate_grounded_answer,
)


ALLOWED_PDF_MIME_TYPES = {"application/pdf"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30 MB

SAMPLE_METADATA: dict[str, dict[str, Any]] = {
    "Cloud_Security_and_Governance_Standard.pdf": {
        "title": "Cloud Security & Governance Standard",
        "category": "Security & Compliance",
        "pages": 3,
        "summary": "Zero-trust VPC segmentation, 90/365-day CMEK key rotation, TLS 1.3, and Sev-1 to Sev-3 response SLAs.",
    },
    "Industrial_Equipment_Maintenance_SOP.pdf": {
        "title": "Industrial Equipment Maintenance SOP",
        "category": "Manufacturing Operations",
        "pages": 3,
        "summary": "Coolant pump HPC-5000 nominal flow, ISO 10816 vibration zones, bearing thermal limits, and 5-step LOTO procedure.",
    },
    "Enterprise_Platform_Reliability_Playbook.pdf": {
        "title": "Enterprise Platform Reliability Playbook",
        "category": "Site Reliability",
        "pages": 3,
        "summary": "Multi-region HA architecture, Tier-0/1/2 RPO & RTO matrix, 35-day PITR window, and change-freeze rules.",
    },
}


class ChatTurn(BaseModel):
    """Single historical turn sent with a conversational follow-up."""

    role: str = Field(..., pattern="^(user|assistant|model)$")
    content: str = Field(..., min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    """Request payload for grounded RAG question answering."""

    question: str = Field(..., min_length=1, max_length=4000)
    document_id: int | None = Field(default=None)
    top_k: int = Field(default=5, ge=1, le=12)
    history: list[ChatTurn] = Field(default_factory=list)


app = FastAPI(
    title="From Documents to Data: Document AI, AlloyDB Vector & Grounded Chat",
    description=(
        "Open-source reference blueprint ingesting multi-page enterprise PDFs with "
        "Document AI Layout Parser, vectorizing chunks in AlloyDB for PostgreSQL, "
        "and powering a citation-grounded Gemini chatbot."
    ),
    version="2.0.0",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _safe_sample_path(filename: str) -> Path:
    """Resolves a sample PDF inside SAMPLE_DOCS_DIR while blocking path traversal."""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid sample filename.")
    root = SAMPLE_DOCS_DIR.resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Sample PDF '{filename}' not found.")
    return candidate


def _title_from_filename(filename: str) -> str:
    """Creates a readable title for uploaded PDFs."""
    if filename in SAMPLE_METADATA:
        return str(SAMPLE_METADATA[filename]["title"])
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip() or filename


def _ingest_pdf_end_to_end(file_bytes: bytes, filename: str, mime_type: str) -> dict[str, Any]:
    """Executes Cloud Storage -> Document AI Layout Parser -> Vertex AI Embed -> AlloyDB."""
    settings = get_settings()

    # Stage 1-2: Cloud Storage staging + Document AI Layout Parser chunk extraction
    try:
        docai_result = process_document_pipeline(
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            settings=settings,
        )
    except PipelineConfigError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "gcp_unconfigured", "stage": "config", "message": str(exc)},
        ) from exc
    except PipelineServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "gcp_pipeline_failed", "stage": exc.stage, "message": exc.message},
        ) from exc

    chunks = docai_result["chunks"]

    # Stage 3: Vertex AI Embeddings (RETRIEVAL_DOCUMENT, 768-d)
    t_embed = time.perf_counter()
    try:
        texts_to_embed = [
            f"{chunk['section_heading']}\n{chunk['content']}"
            if chunk.get("section_heading")
            else chunk["content"]
            for chunk in chunks
        ]
        embeddings = embed_texts(
            texts=texts_to_embed,
            settings=settings,
            task_type="RETRIEVAL_DOCUMENT",
        )
    except RagServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "vertex_embedding_failed", "stage": exc.stage, "message": exc.message},
        ) from exc
    embedding_ms = round((time.perf_counter() - t_embed) * 1000, 1)

    # Stage 4: AlloyDB relational + pgvector persistence
    t_db = time.perf_counter()
    doc_meta = {
        "title": _title_from_filename(filename),
        "filename": Path(filename).name,
        "page_count": docai_result["page_count"],
        "source_gcs_uri": docai_result["source_gcs_uri"],
        "output_gcs_uri": docai_result["output_gcs_uri"],
    }
    try:
        db_doc, sql_proof = insert_document_and_chunks(
            settings=settings,
            doc_meta=doc_meta,
            chunks=chunks,
            embeddings=embeddings,
        )
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "alloydb_write_failed",
                "stage": "alloydb_persist",
                "message": str(exc),
                "partial_pipeline": {
                    "source_gcs_uri": docai_result["source_gcs_uri"],
                    "output_gcs_uri": docai_result["output_gcs_uri"],
                    "page_count": docai_result["page_count"],
                    "chunk_count": len(chunks),
                },
            },
        ) from exc
    alloydb_write_ms = round((time.perf_counter() - t_db) * 1000, 1)

    timings = dict(docai_result["timings"])
    timings["embedding_ms"] = embedding_ms
    timings["alloydb_write_ms"] = alloydb_write_ms
    timings["total_ms"] = round(sum(timings.values()), 1)

    return {
        "status": "ok",
        "filename": filename,
        "timings": timings,
        "source_gcs_uri": docai_result["source_gcs_uri"],
        "output_gcs_uri": docai_result["output_gcs_uri"],
        "document": db_doc,
        "chunks_preview": chunks[:8],
        "sql_proof": sql_proof,
        "ddl_sql": load_schema_ddl(),
        "raw_docai_json": docai_result["raw_docai_json"],
    }


@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    """Serves the minimalist web UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    """Checks live Cloud Storage, Document AI, Vertex AI, and AlloyDB connectivity."""
    settings = get_settings()
    gcs_status = check_gcs_health(settings)
    docai_status = check_docai_health(settings)
    vertex_status = check_vertex_health(settings)
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
                "vector_enabled": False,
                "detail": f"Connected to AlloyDB, but auto-schema init failed: {exc}",
            }

    ready = all(
        comp.get("status") == "ok"
        for comp in (gcs_status, docai_status, vertex_status, db_status)
    )

    return {
        "ready": ready,
        "schema_auto_initialized": auto_initialized,
        "ddl_sql": load_schema_ddl(),
        "components": {
            "cloud_storage": gcs_status,
            "document_ai": docai_status,
            "vertex_ai": vertex_status,
            "alloydb": db_status,
        },
    }


@app.get("/api/samples")
def api_list_samples() -> dict[str, Any]:
    """Lists synthetic multi-page sample PDFs available in sample_docs/."""
    samples: list[dict[str, Any]] = []
    if SAMPLE_DOCS_DIR.is_dir():
        for pdf_path in sorted(SAMPLE_DOCS_DIR.glob("*.pdf")):
            meta = SAMPLE_METADATA.get(pdf_path.name, {})
            samples.append(
                {
                    "filename": pdf_path.name,
                    "title": meta.get("title", _title_from_filename(pdf_path.name)),
                    "category": meta.get("category", "Enterprise Document"),
                    "pages": meta.get("pages", 3),
                    "summary": meta.get("summary", "Multi-page synthetic enterprise PDF."),
                    "pdf_url": f"/api/samples/{pdf_path.name}",
                }
            )
    return {"samples": samples}


@app.get("/api/samples/{filename}")
def api_get_sample_file(filename: str) -> FileResponse:
    """Serves a sample PDF safely from sample_docs/."""
    path = _safe_sample_path(filename)
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF specimens are served.")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.post("/api/documents/upload")
async def api_upload_document(file: UploadFile = File(...)) -> JSONResponse:
    """Ingests an uploaded multi-page PDF into Cloud Storage, Document AI, and AlloyDB."""
    content_type = (file.content_type or "").lower()
    ext = Path(file.filename or "").suffix.lower()
    if content_type not in ALLOWED_PDF_MIME_TYPES and ext != ".pdf":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_media_type",
                "message": "Please upload a PDF document (.pdf).",
            },
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=400,
            detail={"error": "empty_file", "message": "Uploaded PDF file is empty."},
        )
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": "file_too_large", "message": "PDF exceeds 30 MB limit."},
        )

    result = _ingest_pdf_end_to_end(
        file_bytes=file_bytes,
        filename=file.filename or "uploaded_document.pdf",
        mime_type="application/pdf",
    )
    return JSONResponse(content=result)


@app.post("/api/documents/sample/{filename}")
def api_ingest_sample_document(filename: str) -> JSONResponse:
    """Ingests one of the synthetic sample PDFs through the live pipeline."""
    path = _safe_sample_path(filename)
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Sample must be a PDF.")

    result = _ingest_pdf_end_to_end(
        file_bytes=path.read_bytes(),
        filename=path.name,
        mime_type="application/pdf",
    )
    return JSONResponse(content=result)


@app.get("/api/documents")
def api_list_documents() -> JSONResponse:
    """Lists all indexed documents and KPI totals directly from AlloyDB."""
    settings = get_settings()
    try:
        rows, summary, sql_proof = list_documents(settings)
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "alloydb_unavailable", "message": str(exc)},
        ) from exc

    return JSONResponse(
        content={
            "documents": rows,
            "summary": summary,
            "sql_proof": sql_proof,
            "ddl_sql": load_schema_ddl(),
        }
    )


@app.get("/api/documents/{document_id}/chunks")
def api_get_document_chunks(document_id: int) -> JSONResponse:
    """Returns ordered layout chunks stored in AlloyDB for a specific document."""
    settings = get_settings()
    try:
        chunks = get_document_chunks(settings, document_id)
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "alloydb_unavailable", "message": str(exc)},
        ) from exc
    return JSONResponse(content={"document_id": document_id, "chunks": chunks})


@app.delete("/api/documents/{document_id}")
def api_delete_document(document_id: int) -> JSONResponse:
    """Deletes an indexed document and its cascading chunks from AlloyDB."""
    settings = get_settings()
    try:
        deleted = delete_document(settings, document_id)
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "alloydb_delete_failed", "message": str(exc)},
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found.")
    return JSONResponse(content={"status": "deleted", "id": document_id})


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


@app.post("/api/chat")
def api_chat(request: ChatRequest) -> JSONResponse:
    """Embeds user question, retrieves hybrid pgvector chunks, and generates grounded answer."""
    question = request.question.strip()
    if not question:
        raise HTTPException(
            status_code=400,
            detail={"error": "empty_question", "message": "Question cannot be blank."},
        )

    settings = get_settings()

    # Step 1: Embed question with RETRIEVAL_QUERY
    t_embed = time.perf_counter()
    try:
        query_vectors = embed_texts(
            texts=[question],
            settings=settings,
            task_type="RETRIEVAL_QUERY",
        )
        query_embedding = query_vectors[0]
    except RagServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "vertex_embedding_failed", "stage": exc.stage, "message": exc.message},
        ) from exc
    embed_query_ms = round((time.perf_counter() - t_embed) * 1000, 1)

    # Step 2: Hybrid pgvector + full-text search in AlloyDB
    t_search = time.perf_counter()
    try:
        retrieved_chunks, sql_proof = hybrid_search_chunks(
            settings=settings,
            query_embedding=query_embedding,
            query_text=question,
            document_id=request.document_id,
            top_k=request.top_k,
        )
    except DatabaseServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "alloydb_search_failed", "stage": "alloydb_search", "message": str(exc)},
        ) from exc
    alloydb_search_ms = round((time.perf_counter() - t_search) * 1000, 1)

    if not retrieved_chunks:
        total_ms = round(embed_query_ms + alloydb_search_ms, 1)
        return JSONResponse(
            content={
                "answer": (
                    "No indexed document chunks were found in AlloyDB for PostgreSQL yet. "
                    "Ingest one or more PDFs in Step 01 first, then ask your question again."
                ),
                "citations": [],
                "timings": {
                    "embed_query_ms": embed_query_ms,
                    "alloydb_search_ms": alloydb_search_ms,
                    "gemini_generate_ms": 0.0,
                    "total_ms": total_ms,
                },
                "sql_proof": sql_proof,
                "ddl_sql": load_schema_ddl(),
            }
        )

    # Step 3: Grounded Gemini answer generation
    try:
        gen_result = generate_grounded_answer(
            question=question,
            retrieved_chunks=retrieved_chunks,
            history=[turn.model_dump() for turn in request.history],
            settings=settings,
        )
    except RagServiceError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "gemini_generation_failed", "stage": exc.stage, "message": exc.message},
        ) from exc

    citations = [
        {
            "citation_index": idx,
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "document_title": row["document_title"],
            "filename": row["filename"],
            "chunk_index": row["chunk_index"],
            "page_start": row["page_start"],
            "page_end": row["page_end"],
            "section_heading": row.get("section_heading"),
            "content": row["content"],
            "vector_similarity": row["vector_similarity"],
            "keyword_score": row["keyword_score"],
            "hybrid_score": row["hybrid_score"],
        }
        for idx, row in enumerate(retrieved_chunks, start=1)
    ]

    gemini_generate_ms = gen_result["latency_ms"]
    total_ms = round(embed_query_ms + alloydb_search_ms + gemini_generate_ms, 1)

    return JSONResponse(
        content={
            "answer": gen_result["answer"],
            "citations": citations,
            "timings": {
                "embed_query_ms": embed_query_ms,
                "alloydb_search_ms": alloydb_search_ms,
                "gemini_generate_ms": gemini_generate_ms,
                "total_ms": total_ms,
            },
            "sql_proof": sql_proof,
            "ddl_sql": load_schema_ddl(),
        }
    )
