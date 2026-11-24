# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for multi-page PDF samples, Layout Parser chunking, and FastAPI endpoints."""

from __future__ import annotations

from pathlib import Path
import base64
import re
import zlib

from fastapi.testclient import TestClient

from app.chunking import clean_chunk_text, extract_chunks_from_docai
from app.db import format_vector_literal
from app.main import app


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DOCS_DIR = REPO_ROOT / "sample_docs"


def _extract_pdf_streams_text(raw_pdf: bytes) -> str:
    """Decompresses ReportLab ASCII85/FlateDecode PDF content streams."""
    texts: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw_pdf, flags=re.DOTALL):
        payload = match.group(1).strip()
        if payload.endswith(b"~>"):
            try:
                decoded = base64.a85decode(payload[:-2], ignorechars=b" \r\n\t")
                texts.append(zlib.decompress(decoded).decode("latin-1", errors="ignore"))
                continue
            except (ValueError, zlib.error):
                pass
        try:
            texts.append(zlib.decompress(payload).decode("latin-1", errors="ignore"))
        except zlib.error:
            texts.append(payload.decode("latin-1", errors="ignore"))
    return "\n".join(texts)


def test_clean_chunk_text_preserves_tables_and_lists() -> None:
    """Verifies clean_chunk_text preserves markdown tables, bullets, and numbers."""
    raw = (
        "## 3. SLA Matrix\r\n\r\n\r\n"
        "| Tier | Ack | Containment |\r\n"
        "| --- | --- | --- |\r\n"
        "| Sev-1 Critical | 15 minutes | 4 hours |\r\n\r\n"
        "- Rotation:   90 days   \n"
    )
    cleaned = clean_chunk_text(raw)
    assert "| Sev-1 Critical | 15 minutes | 4 hours |" in cleaned
    assert "- Rotation: 90 days" in cleaned
    assert "\r" not in cleaned
    assert "\n\n\n" not in cleaned


def test_extract_chunks_from_layout_parser_chunked_document() -> None:
    """Tests primary extraction path from Document AI Layout Parser chunkedDocument."""
    docai_payload = {
        "pages": [{}, {}, {}],
        "chunkedDocument": {
            "chunks": [
                {
                    "chunkId": "c1",
                    "content": "# 2. Cryptographic Standards\nCMEK mandatory; rotate operational secrets every 90 days.",
                    "pageSpan": {"pageStart": 2, "pageEnd": 2},
                    "pageHeaders": [{"text": "SECTION 02 | CRYPTOGRAPHIC CONTROLS"}],
                },
                {
                    "chunkId": "c2",
                    "content": "| Severity | Ack | Containment |\n| --- | --- | --- |\n| Sev-1 Critical | 15 minutes | 4 hours |",
                    "pageSpan": {"pageStart": 3, "pageEnd": 3},
                },
                {
                    "chunkId": "c3",
                    "content": "   ",
                    "pageSpan": {"pageStart": 3, "pageEnd": 3},
                },
            ]
        },
    }

    page_count, chunks = extract_chunks_from_docai(docai_payload)
    assert page_count == 3
    assert len(chunks) == 2

    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["page_start"] == 2
    assert chunks[0]["page_end"] == 2
    assert chunks[0]["section_heading"] == "2. Cryptographic Standards"
    assert "90 days" in chunks[0]["content"]
    assert chunks[0]["token_estimate"] >= 1

    # Inherits prior section heading when chunk has no heading of its own
    assert chunks[1]["chunk_index"] == 1
    assert chunks[1]["page_start"] == 3
    assert chunks[1]["section_heading"] == "2. Cryptographic Standards"
    assert "| Sev-1 Critical | 15 minutes | 4 hours |" in chunks[1]["content"]


def test_extract_chunks_from_document_layout_and_text_fallbacks() -> None:
    """Tests fallback chunking when chunkedDocument is empty."""
    layout_payload = {
        "pages": [{}, {}],
        "documentLayout": {
            "blocks": [
                {
                    "pageSpan": {"pageStart": 1, "pageEnd": 1},
                    "textBlock": {
                        "type": "heading-1",
                        "text": "Pump HPC-5000 Envelope",
                        "blocks": [
                            {
                                "pageSpan": {"pageStart": 1, "pageEnd": 1},
                                "textBlock": {
                                    "type": "paragraph",
                                    "text": "Nominal flow rate 420 L/min at 18.5 bar.",
                                },
                            }
                        ],
                    },
                },
                {
                    "pageSpan": {"pageStart": 2, "pageEnd": 2},
                    "tableBlock": {
                        "headerRows": [{"cells": [{"blocks": [{"textBlock": {"text": "Zone"}}]}]}],
                        "bodyRows": [{"cells": [{"blocks": [{"textBlock": {"text": "Zone D > 7.1 mm/s"}}]}]}],
                    },
                },
            ]
        },
    }
    page_count, chunks = extract_chunks_from_docai(layout_payload)
    assert page_count == 2
    assert len(chunks) == 1
    assert "## Pump HPC-5000 Envelope" in chunks[0]["content"]
    assert "| Zone D > 7.1 mm/s |" in chunks[0]["content"]

    plain_payload = {
        "text": "First page text.\nSecond page text.",
        "pages": [
            {"layout": {"textAnchor": {"textSegments": [{"startIndex": 0, "endIndex": 16}]}}},
            {"layout": {"textAnchor": {"textSegments": [{"startIndex": 17, "endIndex": 34}]}}},
        ],
    }
    p_count, p_chunks = extract_chunks_from_docai(plain_payload)
    assert p_count == 2
    assert len(p_chunks) == 2
    assert p_chunks[0]["page_start"] == 1
    assert p_chunks[1]["page_start"] == 2


def test_format_vector_literal() -> None:
    """Verifies pgvector literal serialization format."""
    literal = format_vector_literal([0.1, -0.25, 0.0])
    assert literal == "[0.100000,-0.250000,0.000000]"


def test_sample_pdfs_exist_multipage_and_clean() -> None:
    """Verifies all 3 synthetic multi-page PDFs exist, have 3 pages, and contain no currency/PII."""
    expected_pdfs = (
        "Cloud_Security_and_Governance_Standard.pdf",
        "Industrial_Equipment_Maintenance_SOP.pdf",
        "Enterprise_Platform_Reliability_Playbook.pdf",
    )
    for filename in expected_pdfs:
        pdf_path = SAMPLE_DOCS_DIR / filename
        assert pdf_path.is_file() and pdf_path.stat().st_size > 4000
        raw = pdf_path.read_bytes()
        assert raw.startswith(b"%PDF-")
        pages = len(re.findall(rb"/Type\s*/Page\b", raw))
        assert pages >= 3, f"{filename} expected >=3 pages, got {pages}"

        stream_text = _extract_pdf_streams_text(raw)
        assert len(stream_text) > 100
        for forbidden in ("$", "£", "€", "altostrat" + ".com", "@" + "google.com"):
            assert forbidden not in stream_text, f"{filename} contained '{forbidden}'"


def test_fastapi_ui_samples_and_chat_validation(monkeypatch) -> None:
    """Tests UI routes, sample PDF serving, and honest error handling without mocks."""
    client = TestClient(app)

    home_resp = client.get("/")
    assert home_resp.status_code == 200
    assert "DOCUMENTS TO DATA" in home_resp.text
    assert "Interactive Document Chatbot" in home_resp.text
    assert "Inter" in home_resp.text
    assert "JetBrains+Mono" in home_resp.text
    assert "Google Sans" not in home_resp.text

    css_resp = client.get("/static/styles.css")
    assert css_resp.status_code == 200

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200

    samples_resp = client.get("/api/samples")
    assert samples_resp.status_code == 200
    samples = samples_resp.json()["samples"]
    assert len(samples) == 3

    pdf_resp = client.get("/api/samples/Cloud_Security_and_Governance_Standard.pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"

    traversal_resp = client.get("/api/samples/..%2Fapp%2Fmain.py")
    assert traversal_resp.status_code in (400, 404)

    # Clear GCP env vars to verify honest unconfigured/error responses (zero mock fallbacks)
    for var in (
        "GCP_PROJECT_ID",
        "DOCAI_PROCESSOR_ID",
        "GCS_INPUT_BUCKET",
        "GCS_OUTPUT_BUCKET",
    ):
        monkeypatch.setenv(var, "")

    health_resp = client.get("/api/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert health_data["ready"] is False
    assert health_data["components"]["cloud_storage"]["status"] == "unconfigured"
    assert health_data["components"]["document_ai"]["status"] == "unconfigured"
    assert health_data["components"]["vertex_ai"]["status"] == "unconfigured"

    sample_ingest_resp = client.post(
        "/api/documents/sample/Cloud_Security_and_Governance_Standard.pdf"
    )
    assert sample_ingest_resp.status_code == 400
    assert sample_ingest_resp.json()["detail"]["error"] == "gcp_unconfigured"

    bad_upload = client.post(
        "/api/documents/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert bad_upload.status_code == 400

    empty_upload = client.post(
        "/api/documents/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert empty_upload.status_code == 400

    blank_chat = client.post("/api/chat", json={"question": "   "})
    assert blank_chat.status_code == 400

    invalid_chat = client.post("/api/chat", json={})
    assert invalid_chat.status_code == 422

    unconfigured_chat = client.post(
        "/api/chat",
        json={"question": "What are the Sev-1 SLAs?"},
    )
    assert unconfigured_chat.status_code == 503
    assert unconfigured_chat.json()["detail"]["error"] == "vertex_embedding_failed"
