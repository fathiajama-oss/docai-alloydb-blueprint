# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Real AlloyDB for PostgreSQL persistence and query layer via psycopg2."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

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
    """Verifies live AlloyDB connectivity and checks whether the target table exists."""
    if not settings.is_db_configured():
        return {
            "status": "unconfigured",
            "schema_initialized": False,
            "detail": "Set DB_HOST, DB_PORT, DB_NAME, and DB_USER in .env.",
        }
    try:
        conn = get_connection(settings)
    except DatabaseServiceError as exc:
        return {
            "status": "error",
            "schema_initialized": False,
            "detail": str(exc),
        }

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    version() AS server_version,
                    current_database() AS database_name,
                    to_regclass('"docprocessing".driverslicense') IS NOT NULL AS table_exists;
                """
            )
            info = dict(cur.fetchone())
        return {
            "status": "ok",
            "host": settings.DB_HOST,
            "port": settings.DB_PORT,
            "database": info["database_name"],
            "version": info["server_version"],
            "schema_initialized": bool(info["table_exists"]),
            "detail": "Connected to AlloyDB for PostgreSQL.",
        }
    except psycopg2.Error as exc:
        return {
            "status": "error",
            "schema_initialized": False,
            "detail": f"AlloyDB health query failed: {exc}",
        }
    finally:
        conn.close()


def insert_license_record(
    settings: Settings,
    record_data: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Inserts a cleaned driver's license record into AlloyDB and returns SQL proof."""
    insert_sql = """
        INSERT INTO "docprocessing".driverslicense (
            dl_number,
            given_name,
            surname,
            address,
            dob,
            expiration_date,
            issue_date,
            source_gcs_uri,
            output_gcs_uri,
            avg_confidence,
            raw_entities
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING
            id,
            dl_number,
            given_name,
            surname,
            address,
            dob,
            expiration_date,
            issue_date,
            source_gcs_uri,
            output_gcs_uri,
            avg_confidence,
            raw_entities,
            processed_at,
            CASE
                WHEN expiration_date IS NULL THEN 'UNKNOWN'
                WHEN expiration_date < CURRENT_DATE THEN 'EXPIRED'
                WHEN expiration_date <= CURRENT_DATE + INTERVAL '365 days' THEN 'EXPIRING_SOON'
                ELSE 'VALID'
            END AS compliance_status;
    """.strip()

    raw_entities = record_data.get("raw_entities") or []
    params = (
        record_data.get("dl_number"),
        record_data.get("given_name"),
        record_data.get("surname"),
        record_data.get("address"),
        record_data.get("dob"),
        record_data.get("expiration_date"),
        record_data.get("issue_date"),
        record_data.get("source_gcs_uri"),
        record_data.get("output_gcs_uri"),
        record_data.get("avg_confidence"),
        Json(raw_entities),
    )

    conn = get_connection(settings)
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(insert_sql, params)
                inserted = dict(cur.fetchone())
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Failed to insert record into AlloyDB: {exc}") from exc
    finally:
        conn.close()

    def _sql_literal(val: Any) -> str:
        if val is None:
            return "NULL"
        if isinstance(val, date):
            return f"'{val.isoformat()}'"
        if isinstance(val, (int, float)):
            return str(val)
        escaped = str(val).replace("'", "''")
        return f"'{escaped}'"

    sql_proof = (
        "-- Executed parameterized INSERT against AlloyDB for PostgreSQL\n"
        'INSERT INTO "docprocessing".driverslicense (\n'
        "    dl_number, given_name, surname, address, dob,\n"
        "    expiration_date, issue_date, source_gcs_uri,\n"
        "    output_gcs_uri, avg_confidence, raw_entities\n"
        ") VALUES (\n"
        f"    {_sql_literal(record_data.get('dl_number'))},\n"
        f"    {_sql_literal(record_data.get('given_name'))},\n"
        f"    {_sql_literal(record_data.get('surname'))},\n"
        f"    {_sql_literal(record_data.get('address'))},\n"
        f"    {_sql_literal(record_data.get('dob'))},\n"
        f"    {_sql_literal(record_data.get('expiration_date'))},\n"
        f"    {_sql_literal(record_data.get('issue_date'))},\n"
        f"    {_sql_literal(record_data.get('source_gcs_uri'))},\n"
        f"    {_sql_literal(record_data.get('output_gcs_uri'))},\n"
        f"    {_sql_literal(record_data.get('avg_confidence'))},\n"
        f"    '{json.dumps(raw_entities)[:180]}...'::jsonb\n"
        ") RETURNING id, processed_at;"
    )

    return _serialize_row(inserted), sql_proof


def query_license_records(
    settings: Settings,
    status_filter: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Queries driver's license records and aggregate compliance metrics from AlloyDB."""
    where_clauses: list[str] = []
    params: list[Any] = []

    if status_filter:
        normalized = status_filter.strip().upper()
        if normalized == "EXPIRED":
            where_clauses.append("expiration_date < CURRENT_DATE")
        elif normalized == "EXPIRING_SOON":
            where_clauses.append(
                "expiration_date >= CURRENT_DATE AND expiration_date <= CURRENT_DATE + INTERVAL '365 days'"
            )
        elif normalized == "VALID":
            where_clauses.append("expiration_date > CURRENT_DATE + INTERVAL '365 days'")

    if search and search.strip():
        term = f"%{search.strip()}%"
        where_clauses.append(
            "(dl_number ILIKE %s OR given_name ILIKE %s OR surname ILIKE %s OR address ILIKE %s)"
        )
        params.extend([term, term, term, term])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    select_sql = f"""
        SELECT
            id,
            dl_number,
            given_name,
            surname,
            address,
            dob,
            expiration_date,
            issue_date,
            source_gcs_uri,
            output_gcs_uri,
            avg_confidence,
            raw_entities,
            processed_at,
            CASE
                WHEN expiration_date IS NULL THEN 'UNKNOWN'
                WHEN expiration_date < CURRENT_DATE THEN 'EXPIRED'
                WHEN expiration_date <= CURRENT_DATE + INTERVAL '365 days' THEN 'EXPIRING_SOON'
                ELSE 'VALID'
            END AS compliance_status
        FROM "docprocessing".driverslicense
        {where_sql}
        ORDER BY processed_at DESC, id DESC
        LIMIT %s;
    """.strip()

    summary_sql = """
        SELECT
            COUNT(*)::int AS total,
            COUNT(*) FILTER (
                WHERE expiration_date > CURRENT_DATE + INTERVAL '365 days'
            )::int AS valid,
            COUNT(*) FILTER (
                WHERE expiration_date >= CURRENT_DATE
                  AND expiration_date <= CURRENT_DATE + INTERVAL '365 days'
            )::int AS expiring_soon,
            COUNT(*) FILTER (
                WHERE expiration_date < CURRENT_DATE
            )::int AS expired,
            ROUND(AVG(avg_confidence)::numeric, 4) AS avg_confidence
        FROM "docprocessing".driverslicense;
    """.strip()

    conn = get_connection(settings)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(select_sql, [*params, limit])
            rows = [_serialize_row(dict(r)) for r in cur.fetchall()]

            cur.execute(summary_sql)
            raw_summary = dict(cur.fetchone() or {})
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Failed to query records from AlloyDB: {exc}") from exc
    finally:
        conn.close()

    summary = {
        "total": int(raw_summary.get("total") or 0),
        "valid": int(raw_summary.get("valid") or 0),
        "expiring_soon": int(raw_summary.get("expiring_soon") or 0),
        "expired": int(raw_summary.get("expired") or 0),
        "avg_confidence": (
            float(raw_summary["avg_confidence"])
            if raw_summary.get("avg_confidence") is not None
            else None
        ),
    }

    return rows, summary, select_sql


def delete_license_record(settings: Settings, record_id: int) -> bool:
    """Deletes a record by primary key from AlloyDB. Returns True if a row was deleted."""
    conn = get_connection(settings)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    'DELETE FROM "docprocessing".driverslicense WHERE id = %s RETURNING id;',
                    (record_id,),
                )
                return cur.fetchone() is not None
    except psycopg2.Error as exc:
        raise DatabaseServiceError(f"Failed to delete record {record_id}: {exc}") from exc
    finally:
        conn.close()
