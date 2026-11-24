-- Copyright 2026 Google LLC
-- SPDX-License-Identifier: Apache-2.0

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS "docprocessing";

CREATE TABLE IF NOT EXISTS "docprocessing".documents (
    id SERIAL PRIMARY KEY,
    title VARCHAR NOT NULL,
    filename VARCHAR NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 1,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    source_gcs_uri VARCHAR NOT NULL,
    output_gcs_uri VARCHAR NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS "docprocessing".document_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES "docprocessing".documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    page_start INTEGER NOT NULL DEFAULT 1,
    page_end INTEGER NOT NULL DEFAULT 1,
    section_heading VARCHAR,
    content TEXT NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    embedding vector(768) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_doc_id
    ON "docprocessing".document_chunks (document_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON "docprocessing".document_chunks
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_document_chunks_fts
    ON "docprocessing".document_chunks
    USING gin (to_tsvector('english', coalesce(section_heading, '') || ' ' || content));
