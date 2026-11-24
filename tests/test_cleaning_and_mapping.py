# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for cleaning, Document AI mapping, samples, and FastAPI endpoints."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.cleaning import (
    clean_address_text,
    clean_text,
    extract_driver_license_record,
    parse_document_date,
)
from app.main import app


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DOCS_DIR = REPO_ROOT / "sample_docs"


def test_clean_text_matches_blueprint() -> None:
    """Verifies punctuation removal and whitespace normalization."""
    assert clean_text("D-8492017!\n") == "D8492017"
    assert clean_text("  ELENA,  MARIE. ") == "ELENA MARIE"
    assert clean_text("") == ""
    assert clean_text(None) == ""


def test_clean_address_text_preserves_address_punctuation() -> None:
    """Verifies readable street address preservation."""
    raw = "1420 BAYSHORE BLVD APT #4B\nSAN FRANCISCO, CA 94124-1200"
    assert clean_address_text(raw) == "1420 BAYSHORE BLVD APT #4B SAN FRANCISCO, CA 94124-1200"


def test_parse_document_date_normalized_and_fallback() -> None:
    """Verifies normalized DateValue preference and multi-format string fallbacks."""
    assert parse_document_date("08/24/1991", {"year": 1991, "month": 8, "day": 24}) == date(1991, 8, 24)
    assert parse_document_date("01/15/2027", None) == date(2027, 1, 15)
    assert parse_document_date("2025-04-02", None) == date(2025, 4, 2)
    assert parse_document_date("04-02-2020", None) == date(2020, 4, 2)
    assert parse_document_date("DOB 08/24/1991", None) == date(1991, 8, 24)
    assert parse_document_date("not-a-date", None) is None


def test_extract_driver_license_record_mapping_and_tiebreak() -> None:
    """Tests Document AI entity mapping, highest-confidence tiebreak, and bounding boxes."""
    sample_docai_json = {
        "entities": [
            {
                "type": "Document Id",
                "mentionText": "D849-2017!",
                "confidence": 0.92,
                "pageAnchor": {
                    "pageRefs": [
                        {
                            "page": "0",
                            "boundingPoly": {
                                "normalizedVertices": [
                                    {"x": 0.38, "y": 0.25},
                                    {"x": 0.55, "y": 0.25},
                                    {"x": 0.55, "y": 0.30},
                                    {"x": 0.38, "y": 0.30},
                                ]
                            },
                        }
                    ]
                },
            },
            # Higher confidence duplicate should win
            {
                "type": "dl_number",
                "mentionText": "D8492017",
                "confidence": 0.99,
                "pageAnchor": {
                    "pageRefs": [
                        {
                            "page": "0",
                            "boundingPoly": {
                                "normalizedVertices": [
                                    {"y": 0.25},
                                    {"x": 0.55, "y": 0.25},
                                    {"x": 0.55, "y": 0.30},
                                    {"y": 0.30},
                                ]
                            },
                        }
                    ]
                },
            },
            {
                "type": "Given Names",
                "mentionText": "ELENA MARIE",
                "confidence": 0.97,
            },
            {
                "type": "Family Name",
                "mentionText": "RIVERS\n",
                "confidence": 0.98,
            },
            {
                "type": "Address",
                "mentionText": "1420 BAYSHORE BLVD APT 4B\nSAN FRANCISCO, CA 94124",
                "confidence": 0.94,
            },
            {
                "type": "Date Of Birth",
                "mentionText": "08/24/1991",
                "confidence": 0.96,
                "normalizedValue": {"dateValue": {"year": 1991, "month": 8, "day": 24}},
            },
            {
                "type": "Expiration Date",
                "mentionText": "08/24/2029",
                "confidence": 0.95,
                "normalizedValue": {"dateValue": {"year": 2029, "month": 8, "day": 24}},
            },
            {
                "type": "Issue Date",
                "mentionText": "03/15/2023",
                "confidence": 0.95,
            },
        ]
    }

    extracted = extract_driver_license_record(sample_docai_json)
    assert extracted["dl_number"] == "D8492017"
    assert extracted["given_name"] == "ELENA MARIE"
    assert extracted["surname"] == "RIVERS"
    assert extracted["address"] == "1420 BAYSHORE BLVD APT 4B SAN FRANCISCO, CA 94124"
    assert extracted["dob"] == date(1991, 8, 24)
    assert extracted["expiration_date"] == date(2029, 8, 24)
    assert extracted["issue_date"] == date(2023, 3, 15)
    assert extracted["avg_confidence"] == round((0.99 + 0.97 + 0.98 + 0.94 + 0.96 + 0.95 + 0.95) / 7, 4)

    # Default-omitted protobuf x=0.0 is preserved cleanly
    dl_box = extracted["bounding_boxes"]["dl_number"]
    assert len(dl_box) == 4
    assert dl_box[0] == {"x": 0.0, "y": 0.25}


def test_synthetic_sample_documents_exist_and_valid() -> None:
    """Verifies all 3 synthetic driver's license PNGs and PDFs exist and are valid."""
    stems = (
        "sample_dl_california_valid",
        "sample_dl_texas_expiring",
        "sample_dl_newyork_expired",
    )
    for stem in stems:
        png_path = SAMPLE_DOCS_DIR / f"{stem}.png"
        pdf_path = SAMPLE_DOCS_DIR / f"{stem}.pdf"
        assert png_path.is_file() and png_path.stat().st_size > 10_000
        assert pdf_path.is_file() and pdf_path.stat().st_size > 10_000

        with Image.open(png_path) as img:
            assert img.size == (1100, 700)
            assert img.format == "PNG"

        assert pdf_path.read_bytes().startswith(b"%PDF-")


def test_fastapi_ui_and_samples_endpoints(monkeypatch) -> None:
    """Tests UI static serving, sample listing, and honest unconfigured pipeline behavior."""
    client = TestClient(app)

    home_resp = client.get("/")
    assert home_resp.status_code == 200
    assert "DOCUMENTS TO DATA" in home_resp.text
    assert "Inter" in home_resp.text
    assert "JetBrains+Mono" in home_resp.text
    assert "Google Sans" not in home_resp.text

    css_resp = client.get("/static/styles.css")
    assert css_resp.status_code == 200
    assert "JetBrains Mono" in css_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200

    samples_resp = client.get("/api/samples")
    assert samples_resp.status_code == 200
    samples = samples_resp.json()["samples"]
    assert len(samples) == 3

    img_resp = client.get("/api/samples/sample_dl_california_valid.png")
    assert img_resp.status_code == 200
    assert img_resp.headers["content-type"] == "image/png"

    traversal_resp = client.get("/api/samples/..%2Fapp%2Fmain.py")
    assert traversal_resp.status_code in (400, 404)

    # Clear GCP env vars to ensure honest unconfigured error (never mock fallback)
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

    sample_proc_resp = client.post("/api/process/sample/sample_dl_california_valid.png")
    assert sample_proc_resp.status_code == 400
    assert sample_proc_resp.json()["detail"]["error"] == "gcp_unconfigured"

    bad_upload = client.post(
        "/api/process/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert bad_upload.status_code == 400
