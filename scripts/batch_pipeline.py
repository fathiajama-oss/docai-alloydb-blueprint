# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Standalone CLI for multi-page PDF ingestion, vectorization, and grounded Q&A.

Uploads PDF documents to Cloud Storage, runs Document AI Layout Parser
(online `process_document` or asynchronous `batch_process_documents`),
generates 768-d embeddings via Vertex AI, persists vectors into AlloyDB for
PostgreSQL (`pgvector`), and optionally runs a hybrid search + Gemini query.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Iterable

# Ensure project root is importable when invoked as `python scripts/batch_pipeline.py`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.chunking import extract_chunks_from_docai  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import (  # noqa: E402
    hybrid_search_chunks,
    init_db,
    insert_document_and_chunks,
)
from app.docai_service import (  # noqa: E402
    batch_process_gcs_documents,
    process_document_pipeline,
    upload_bytes_to_gcs,
)
from app.rag_service import embed_texts, generate_grounded_answer  # noqa: E402


def _discover_pdfs(input_dir: Path) -> list[Path]:
    """Discovers PDF files inside `input_dir`."""
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    return sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


def _readable_title(filename: str) -> str:
    """Formats a human-readable document title from a PDF filename."""
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip() or filename


def _embed_chunks(chunks: list[dict], settings) -> list[list[float]]:
    """Builds contextual chunk strings and fetches 768-d Vertex AI embeddings."""
    texts = [
        f"{chunk['section_heading']}\n{chunk['content']}"
        if chunk.get("section_heading")
        else chunk["content"]
        for chunk in chunks
    ]
    return embed_texts(texts=texts, settings=settings, task_type="RETRIEVAL_DOCUMENT")


def run_online(files: Iterable[Path]) -> int:
    """Processes each PDF synchronously via Document AI Layout Parser and AlloyDB."""
    settings = get_settings()
    init_db(settings)

    processed = 0
    for path in files:
        print(f"[online] Processing {path.name}...")
        docai_res = process_document_pipeline(
            file_bytes=path.read_bytes(),
            filename=path.name,
            mime_type="application/pdf",
            settings=settings,
        )
        chunks = docai_res["chunks"]
        embeddings = _embed_chunks(chunks, settings)
        doc_row, _ = insert_document_and_chunks(
            settings=settings,
            doc_meta={
                "title": _readable_title(path.name),
                "filename": path.name,
                "page_count": docai_res["page_count"],
                "source_gcs_uri": docai_res["source_gcs_uri"],
                "output_gcs_uri": docai_res["output_gcs_uri"],
            },
            chunks=chunks,
            embeddings=embeddings,
        )
        processed += 1
        print(
            f"  -> Persisted id={doc_row['id']} pages={doc_row['page_count']} "
            f"chunks={doc_row['chunk_count']} source={docai_res['source_gcs_uri']}"
        )
    return processed


def run_batch(files: list[Path], timeout: int) -> int:
    """Uploads PDFs to Cloud Storage, runs BatchProcessDocuments, embeds, and persists."""
    settings = get_settings()
    missing = settings.missing_gcp_fields()
    if missing:
        raise RuntimeError(f"Missing Google Cloud settings in .env: {', '.join(missing)}")

    init_db(settings)

    utc_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    input_prefix = f"batch/{utc_ts}/input/"
    output_prefix = f"batch/{utc_ts}/output/"

    for path in files:
        blob_name = f"{input_prefix}{path.name}"
        uri = upload_bytes_to_gcs(
            bucket_name=settings.GCS_INPUT_BUCKET,
            blob_name=blob_name,
            data=path.read_bytes(),
            content_type="application/pdf",
            project_id=settings.GCP_PROJECT_ID,
        )
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
        page_count, chunks = extract_chunks_from_docai(doc_dict)
        if not chunks:
            print(f"  [skip] No extractable chunks in {output_uri}")
            continue
        raw_stem = Path(output_uri).stem
        clean_stem = re.sub(r"-\d+$", "", raw_stem)
        inferred_filename = f"{clean_stem}.pdf"
        embeddings = _embed_chunks(chunks, settings)
        doc_row, _ = insert_document_and_chunks(
            settings=settings,
            doc_meta={
                "title": _readable_title(inferred_filename),
                "filename": inferred_filename,
                "page_count": page_count,
                "source_gcs_uri": f"{gcs_input_prefix_uri}{inferred_filename}",
                "output_gcs_uri": output_uri,
            },
            chunks=chunks,
            embeddings=embeddings,
        )
        processed += 1
        print(
            f"  -> Persisted id={doc_row['id']} file={inferred_filename} "
            f"pages={doc_row['page_count']} chunks={doc_row['chunk_count']}"
        )
    return processed


def run_query(question: str, top_k: int) -> None:
    """Executes a hybrid pgvector search and prints a grounded Gemini answer."""
    settings = get_settings()
    print(f"\n[query] Question: {question}")
    q_vec = embed_texts([question], settings=settings, task_type="RETRIEVAL_QUERY")[0]
    chunks, _ = hybrid_search_chunks(
        settings=settings,
        query_embedding=q_vec,
        query_text=question,
        top_k=top_k,
    )
    if not chunks:
        print("[query] No chunks matched in AlloyDB.")
        return

    res = generate_grounded_answer(
        question=question,
        retrieved_chunks=chunks,
        history=None,
        settings=settings,
    )
    print("\n--- Grounded Answer ---")
    print(res["answer"])
    print("\n--- Retrieved Citations ---")
    for idx, ch in enumerate(chunks, start=1):
        print(
            f"[{idx}] {ch['document_title']} (Pages {ch['page_start']}-{ch['page_end']}, "
            f"Section: {ch.get('section_heading') or 'General'}, "
            f"Cosine: {ch['vector_similarity']}, Hybrid: {ch['hybrid_score']})"
        )


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Ingest multi-page PDFs with Document AI Layout Parser and query via AlloyDB + Gemini."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "sample_docs",
        help="Directory containing PDF documents (default: sample_docs).",
    )
    parser.add_argument(
        "--mode",
        choices=("online", "batch"),
        default="online",
        help="Use synchronous online processing or asynchronous GCS batch processing.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for Document AI batch operations (default: 300).",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="",
        help="Optional question to run against AlloyDB pgvector + Gemini after ingestion.",
    )
    parser.add_argument(
        "--query-only",
        action="store_true",
        help="Skip ingestion and only run --query against already indexed documents.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of top chunks to retrieve for --query (default: 5).",
    )
    args = parser.parse_args()

    if not args.query_only:
        files = _discover_pdfs(args.input_dir)
        if not files:
            print(f"No PDF documents found in {args.input_dir}", file=sys.stderr)
            return 1

        if args.mode == "online":
            count = run_online(files)
        else:
            count = run_batch(files, timeout=args.timeout)
        print(f"Completed ingesting {count} PDF document(s).")

    if args.query.strip():
        run_query(args.query.strip(), top_k=args.top_k)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
