-- Copyright 2026 Google LLC
-- SPDX-License-Identifier: Apache-2.0

CREATE SCHEMA IF NOT EXISTS "docprocessing";

CREATE TABLE IF NOT EXISTS "docprocessing".driverslicense (
    id SERIAL PRIMARY KEY,
    dl_number VARCHAR,
    given_name VARCHAR,
    surname VARCHAR,
    address VARCHAR,
    dob DATE,
    expiration_date DATE,
    issue_date DATE,
    source_gcs_uri VARCHAR,
    output_gcs_uri VARCHAR,
    avg_confidence NUMERIC(5, 4),
    raw_entities JSONB,
    processed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_driverslicense_dl_number
    ON "docprocessing".driverslicense (dl_number);
CREATE INDEX IF NOT EXISTS idx_driverslicense_expiration
    ON "docprocessing".driverslicense (expiration_date);
