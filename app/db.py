# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Real AlloyDB for PostgreSQL persistence and pgvector hybrid search via psycopg2."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

from app.config import SQL_DIR, Settings


SCHEMA_FILE = SQL_DIR / "01_init_schema.sql"


class DatabaseServiceError(RuntimeError):
    """Raised when AlloyDB connection or SQL execution fails."""


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Converts PostgreSQL date/datetime/Decimal values into JSON-safe types."""
    serialized: dict[str, Any] = {}
    for key, val in row.items():
        if isinstance(val, (date, datetime)):
            serialized[key] = val.isoformat()
        elif isinstance(val, Decimal):
            serialized[key] = float(val)
        else:
            serialized[key] = val
    return serialized


def format_vector_literal(embedding: list[float]) -> str:
    """Formats a Python float list into a pgvector literal string '[x1,x2,...]'."""
    return "[" + ",".join(f"{float(v):.6f}" for v in embedding) + "]"


def get_connection(settings: Settings) -> psycopg2.extensions.connection:
    """Opens a live PostgreSQL / AlloyDB connection."""
    try:
        return psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            connect_timeout=settings.DB_CONNECT_TIMEOUT,
        )
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Unable to connect to AlloyDB: {exc}") from exc


def load_schema_ddl() -> str:
    """Loads the canonical schema DDL from sql/01_init_schema.sql."""
    return SCHEMA_FILE.read_text(encoding="utf-8")


def init_db(settings: Settings) -> str:
    """Applies sql/01_init_schema.sql to the configured AlloyDB database."""
    ddl = load_schema_ddl()
    conn = get_connection(settings)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
        return ddl
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Failed to initialize AlloyDB schema: {exc}") from exc
    finally:
        conn.close()


def check_db_health(settings: Settings) -> dict[str, Any]:
    """Verifies live AlloyDB connectivity, pgvector extension, and target tables."""
    if not settings.is_db_configured():
        return {
            "status": "unconfigured",
            "schema_initialized": False,
            "vector_enabled": False,
            "detail": "Set DB_HOST, DB_PORT, DB_NAME, and DB_USER in .env.",
        }
    try:
        conn = get_connection(settings)
    except DatabaseServiceError as exc:
        return {
            "status": "error",
            "schema_initialized": False,
            "vector_enabled": False,
            "detail": str(exc),
        }

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    version() AS server_version,
                    current_database() AS database_name,
                    EXISTS (
                        SELECT 1 FROM pg_extension WHERE extname = 'vector'
                    ) AS vector_enabled,
                    to_regclass('"docprocessing".documents') IS NOT NULL AS docs_table_exists,
                    to_regclass('"docprocessing".document_chunks') IS NOT NULL AS chunks_table_exists;
                """
            )
            info = dict(cur.fetchone())

        schema_ready = bool(
            info["vector_enabled"] and info["docs_table_exists"] and info["chunks_table_exists"]
        )
        return {
            "status": "ok",
            "host": settings.DB_HOST,
            "port": settings.DB_PORT,
            "database": info["database_name"],
            "version": info["server_version"],
            "vector_enabled": bool(info["vector_enabled"]),
            "schema_initialized": schema_ready,
            "detail": (
                "Connected to AlloyDB for PostgreSQL (pgvector active)."
                if schema_ready
                else "Connected to AlloyDB; schema initialization required."
            ),
        }
    except psycopg2.Error as exc:
        return {
            "status": "error",
            "schema_initialized": False,
            "vector_enabled": False,
            "detail": f"AlloyDB health query failed: {exc}",
        }
    finally:
        conn.close()


def insert_document_and_chunks(
    settings: Settings,
    doc_meta: dict[str, Any],
    chunks: list[dict[str, Any]],
    embeddings: list[list[float]],
) -> tuple[dict[str, Any], str]:
    """Atomically inserts a document record and its 768-d vector chunks into AlloyDB."""
    if not chunks or len(chunks) != len(embeddings):
        raise DatabaseServiceError(
            f"Chunk/embedding mismatch: {len(chunks)} chunks vs {len(embeddings)} vectors."
        )

    filename = str(doc_meta["filename"])
    title = str(doc_meta["title"])
    page_count = int(doc_meta.get("page_count") or 1)
    source_gcs_uri = str(doc_meta["source_gcs_uri"])
    output_gcs_uri = str(doc_meta["output_gcs_uri"])

    insert_doc_sql = """
        INSERT INTO "docprocessing".documents (
            title,
            filename,
            page_count,
            chunk_count,
            source_gcs_uri,
            output_gcs_uri
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, title, filename, page_count, chunk_count,
                  source_gcs_uri, output_gcs_uri, processed_at;
    """.strip()

    insert_chunks_sql = """
        INSERT INTO "docprocessing".document_chunks (
            document_id,
            chunk_index,
            page_start,
            page_end,
            section_heading,
            content,
            token_estimate,
            embedding
        )
        VALUES %s;
    """.strip()

    conn = get_connection(settings)
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Replace prior index entries when re-ingesting the same filename
                cur.execute(
                    'DELETE FROM "docprocessing".documents WHERE filename = %s;',
                    (filename,),
                )
                cur.execute(
                    insert_doc_sql,
                    (title, filename, page_count, len(chunks), source_gcs_uri, output_gcs_uri),
                )
                doc_row = dict(cur.fetchone())
                doc_id = int(doc_row["id"])

                chunk_rows = [
                    (
                        doc_id,
                        int(chunk["chunk_index"]),
                        int(chunk["page_start"]),
                        int(chunk["page_end"]),
                        chunk.get("section_heading"),
                        str(chunk["content"]),
                        int(chunk.get("token_estimate") or 0),
                        format_vector_literal(vec),
                    )
                    for chunk, vec in zip(chunks, embeddings)
                ]

                execute_values(
                    cur,
                    insert_chunks_sql,
                    chunk_rows,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s::vector)",
                    page_size=100,
                )
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Failed to persist document chunks in AlloyDB: {exc}") from exc
    finally:
        conn.close()

    preview_vec = "[" + ",".join(f"{v:.4f}" for v in embeddings[0][:4]) + ", ... (768 dims)]"
    sql_proof = (
        "-- Executed transactional document + pgvector batch INSERT in AlloyDB\n"
        'DELETE FROM "docprocessing".documents WHERE filename = ' + f"'{filename}';\n\n"
        'INSERT INTO "docprocessing".documents (\n'
        "    title, filename, page_count, chunk_count, source_gcs_uri, output_gcs_uri\n"
        ") VALUES (\n"
        f"    '{title.replace(chr(39), chr(39)*2)}',\n"
        f"    '{filename}', {page_count}, {len(chunks)},\n"
        f"    '{source_gcs_uri}',\n"
        f"    '{output_gcs_uri}'\n"
        ") RETURNING id, processed_at;\n\n"
        f"-- Bulk inserted {len(chunks)} chunks into \"docprocessing\".document_chunks\n"
        'INSERT INTO "docprocessing".document_chunks (\n'
        "    document_id, chunk_index, page_start, page_end,\n"
        "    section_heading, content, token_estimate, embedding\n"
        ") VALUES\n"
        f"    ({doc_id}, 0, {chunks[0]['page_start']}, {chunks[0]['page_end']}, ..., '{preview_vec}'::vector),\n"
        f"    ... ({len(chunks)} total HNSW-indexed rows);"
    )

    return _serialize_row(doc_row), sql_proof


def hybrid_search_chunks(
    settings: Settings,
    query_embedding: list[float],
    query_text: str,
    document_id: int | None = None,
    top_k: int = 5,
) -> tuple[list[dict[str, Any]], str]:
    """Runs hybrid pgvector cosine + PostgreSQL FTS search over document_chunks."""
    vec_literal = format_vector_literal(query_embedding)
    clean_query = query_text.strip()

    hybrid_sql = """
        SELECT
            c.id AS chunk_id,
            c.document_id,
            d.title AS document_title,
            d.filename,
            c.chunk_index,
            c.page_start,
            c.page_end,
            c.section_heading,
            c.content,
            ROUND((1 - (c.embedding <=> %s::vector))::numeric, 4) AS vector_similarity,
            ROUND(
                ts_rank_cd(
                    to_tsvector('english', coalesce(c.section_heading, '') || ' ' || c.content),
                    websearch_to_tsquery('english', %s)
                )::numeric,
                4
            ) AS keyword_score,
            ROUND(
                (
                    0.82 * (1 - (c.embedding <=> %s::vector))
                    + 0.18 * LEAST(
                        ts_rank_cd(
                            to_tsvector('english', coalesce(c.section_heading, '') || ' ' || c.content),
                            websearch_to_tsquery('english', %s)
                        ),
                        1.0
                    )
                )::numeric,
                4
            ) AS hybrid_score
        FROM "docprocessing".document_chunks c
        JOIN "docprocessing".documents d ON d.id = c.document_id
        WHERE (%s::integer IS NULL OR c.document_id = %s::integer)
        ORDER BY hybrid_score DESC, c.embedding <=> %s::vector ASC
        LIMIT %s;
    """.strip()

    conn = get_connection(settings)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                hybrid_sql,
                (
                    vec_literal,
                    clean_query,
                    vec_literal,
                    clean_query,
                    document_id,
                    document_id,
                    vec_literal,
                    top_k,
                ),
            )
            rows = [_serialize_row(dict(r)) for r in cur.fetchall()]
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Hybrid vector search failed in AlloyDB: {exc}") from exc
    finally:
        conn.close()

    preview_vec = "[" + ",".join(f"{v:.4f}" for v in query_embedding[:4]) + ", ... (768 dims)]"
    escaped_q = clean_query.replace("'", "''")
    doc_filter_sql = "NULL" if document_id is None else str(int(document_id))
    sql_proof = (
        "-- Executed AlloyDB pgvector cosine + GIN full-text hybrid search\n"
        f"-- Query: '{escaped_q}' | Document filter: {doc_filter_sql} | Limit: {top_k}\n"
        "SELECT\n"
        "    c.id AS chunk_id,\n"
        "    d.title AS document_title,\n"
        "    c.page_start,\n"
        "    c.page_end,\n"
        "    c.section_heading,\n"
        f"    ROUND((1 - (c.embedding <=> '{preview_vec}'::vector))::numeric, 4) AS vector_similarity,\n"
        "    ROUND(ts_rank_cd(to_tsvector('english', coalesce(c.section_heading, '') || ' ' || c.content),\n"
        f"                     websearch_to_tsquery('english', '{escaped_q}'))::numeric, 4) AS keyword_score,\n"
        "    ROUND((0.82 * (1 - (c.embedding <=> ...::vector)) + 0.18 * LEAST(ts_rank_cd(...), 1.0))::numeric, 4) AS hybrid_score\n"
        'FROM "docprocessing".document_chunks c\n'
        'JOIN "docprocessing".documents d ON d.id = c.document_id\n'
        f"WHERE ({doc_filter_sql}::integer IS NULL OR c.document_id = {doc_filter_sql}::integer)\n"
        "ORDER BY hybrid_score DESC, c.embedding <=> ...::vector ASC\n"
        f"LIMIT {top_k};"
    )

    return rows, sql_proof


def list_documents(settings: Settings) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Lists all indexed PDF documents and aggregate KPI totals from AlloyDB."""
    select_sql = """
        SELECT
            id,
            title,
            filename,
            page_count,
            chunk_count,
            source_gcs_uri,
            output_gcs_uri,
            processed_at
        FROM "docprocessing".documents
        ORDER BY processed_at DESC, id DESC;
    """.strip()

    summary_sql = """
        SELECT
            COUNT(*)::int AS document_count,
            COALESCE(SUM(chunk_count), 0)::int AS total_chunks,
            COALESCE(SUM(page_count), 0)::int AS total_pages
        FROM "docprocessing".documents;
    """.strip()

    conn = get_connection(settings)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(select_sql)
            rows = [_serialize_row(dict(r)) for r in cur.fetchall()]
            cur.execute(summary_sql)
            raw_summary = dict(cur.fetchone() or {})
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Failed to list indexed documents in AlloyDB: {exc}") from exc
    finally:
        conn.close()

    summary = {
        "document_count": int(raw_summary.get("document_count") or 0),
        "total_chunks": int(raw_summary.get("total_chunks") or 0),
        "total_pages": int(raw_summary.get("total_pages") or 0),
        "embedding_dimensions": settings.EMBEDDING_DIMENSIONS,
    }
    return rows, summary, select_sql


def get_document_chunks(settings: Settings, document_id: int) -> list[dict[str, Any]]:
    """Fetches stored layout chunks for a document without exposing raw 768-float arrays."""
    query_sql = """
        SELECT
            id,
            document_id,
            chunk_index,
            page_start,
            page_end,
            section_heading,
            content,
            token_estimate,
            vector_dims(embedding) AS embedding_dims,
            created_at
        FROM "docprocessing".document_chunks
        WHERE document_id = %s
        ORDER BY chunk_index ASC;
    """.strip()

    conn = get_connection(settings)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query_sql, (document_id,))
            return [_serialize_row(dict(r)) for r in cur.fetchall()]
    except psycopg2.Error as exc:
        raise DatabaseServiceError(
            f"Failed to load chunks for document {document_id}: {exc}"
        ) from exc
    finally:
        conn.close()


def delete_document(settings: Settings, document_id: int) -> bool:
    """Deletes a document and its cascading vector chunks by ID."""
    conn = get_connection(settings)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    'DELETE FROM "docprocessing".documents WHERE id = %s RETURNING id;',
                    (document_id,),
                )
                return cur.fetchone() is not None
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Failed to delete document {document_id}: {exc}") from exc
    finally:
        conn.close()
