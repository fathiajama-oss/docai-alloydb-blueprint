# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Data cleaning, date parsing, and entity-to-schema mapping for Document AI output."""

from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any


_ENTITY_FIELD_MAP: dict[str, str] = {
    "document id": "dl_number",
    "dl number": "dl_number",
    "driver license number": "dl_number",
    "drivers license number": "dl_number",
    "license number": "dl_number",
    "id": "dl_number",
    "given names": "given_name",
    "given name": "given_name",
    "first name": "given_name",
    "family name": "surname",
    "surname": "surname",
    "last name": "surname",
    "address": "address",
    "residential address": "address",
    "date of birth": "dob",
    "dob": "dob",
    "birth date": "dob",
    "expiration date": "expiration_date",
    "exp date": "expiration_date",
    "expiry date": "expiration_date",
    "issue date": "issue_date",
    "iss date": "issue_date",
    "issued date": "issue_date",
}

_DATE_FORMATS: tuple[str, ...] = (
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%m-%d-%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%m/%d/%y",
)


def clean_text(text: str | None) -> str:
    """Strips non-alphanumeric punctuation and normalizes whitespace.

    Implements the blog blueprint cleaning pattern (`re.sub(r"[^\\w\\s]", "", text)`)
    while collapsing multi-line OCR artifacts into single spaces.
    """
    if not text:
        return ""
    stripped = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


def clean_address_text(text: str | None) -> str:
    """Cleans address strings while preserving commas, unit markers, and hyphens."""
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s,#\-./]", "", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_document_date(
    raw_text: str | None,
    normalized_date: dict[str, Any] | str | None = None,
) -> date | None:
    """Parses a Document AI date entity from normalized DateValue or raw text."""
    if isinstance(normalized_date, dict):
        year = normalized_date.get("year")
        month = normalized_date.get("month")
        day = normalized_date.get("day")
        if all(isinstance(v, int) and v > 0 for v in (year, month, day)):
            try:
                return date(int(year), int(month), int(day))
            except ValueError:
                pass
    elif isinstance(normalized_date, str) and normalized_date.strip():
        candidate = normalized_date.strip()
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue

    if not raw_text:
        return None

    candidate = re.sub(r"\s+", " ", raw_text).strip()
    # Strip optional AAMVA prefix labels if captured inside mentionText
    candidate = re.sub(r"^(?:DOB|EXP|ISS)\s*[:\-]?\s*", "", candidate, flags=re.IGNORECASE).strip()

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_entity_type(entity_type: str | None) -> str:
    """Normalizes Document AI entity type labels for case-insensitive lookup."""
    if not entity_type:
        return ""
    normalized = entity_type.strip().lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", normalized)


def _extract_bounding_poly(entity: dict[str, Any]) -> tuple[int, list[dict[str, float]]]:
    """Extracts page number and normalized bounding polygon vertices from an entity."""
    page_anchor = entity.get("pageAnchor") or entity.get("page_anchor") or {}
    page_refs = page_anchor.get("pageRefs") or page_anchor.get("page_refs") or []
    if not page_refs:
        return 0, []

    first_ref = page_refs[0] or {}
    raw_page = first_ref.get("page", 0)
    try:
        page_idx = int(raw_page)
    except (TypeError, ValueError):
        page_idx = 0

    bounding_poly = first_ref.get("boundingPoly") or first_ref.get("bounding_poly") or {}
    norm_vertices = (
        bounding_poly.get("normalizedVertices")
        or bounding_poly.get("normalized_vertices")
        or []
    )

    points: list[dict[str, float]] = []
    for vertex in norm_vertices:
        if not isinstance(vertex, dict):
            continue
        x = float(vertex.get("x", 0.0) or 0.0)
        y = float(vertex.get("y", 0.0) or 0.0)
        points.append({"x": round(x, 5), "y": round(y, 5)})

    return page_idx, points


def _extract_normalized_date_dict(entity: dict[str, Any]) -> dict[str, Any] | str | None:
    """Reads normalizedValue.dateValue (camelCase or snake_case) from an entity."""
    norm_val = entity.get("normalizedValue") or entity.get("normalized_value")
    if not isinstance(norm_val, dict):
        return None
    date_val = norm_val.get("dateValue") or norm_val.get("date_value")
    if isinstance(date_val, dict):
        return date_val
    text_val = norm_val.get("text")
    return text_val if isinstance(text_val, str) else None


def extract_driver_license_record(document_dict: dict[str, Any]) -> dict[str, Any]:
    """Maps a Document AI document dictionary into a structured license record.

    Selects the highest-confidence entity whenever duplicate entity types appear,
    computes average confidence, and preserves normalized bounding boxes per field.
    """
    entities = document_dict.get("entities") or []
    best_by_field: dict[str, dict[str, Any]] = {}
    all_raw_entities: list[dict[str, Any]] = []

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        raw_type = str(entity.get("type") or entity.get("type_") or "")
        mention_text = str(entity.get("mentionText") or entity.get("mention_text") or "")
        confidence = float(entity.get("confidence", 0.0) or 0.0)
        page_idx, polygon = _extract_bounding_poly(entity)
        norm_date = _extract_normalized_date_dict(entity)

        norm_key = _normalize_entity_type(raw_type)
        canonical_field = _ENTITY_FIELD_MAP.get(norm_key)

        all_raw_entities.append(
            {
                "type": raw_type,
                "canonical_field": canonical_field,
                "mention_text": mention_text,
                "confidence": round(confidence, 4),
                "page": page_idx,
                "bounding_poly": polygon,
            }
        )

        if not canonical_field:
            continue

        existing = best_by_field.get(canonical_field)
        if existing is None or confidence > existing["confidence"]:
            best_by_field[canonical_field] = {
                "field": canonical_field,
                "entity_type": raw_type,
                "raw_value": mention_text,
                "confidence": round(confidence, 4),
                "page": page_idx,
                "bounding_poly": polygon,
                "normalized_date": norm_date,
            }

    dl_number = clean_text(best_by_field.get("dl_number", {}).get("raw_value")) or None
    given_name = clean_text(best_by_field.get("given_name", {}).get("raw_value")) or None
    surname = clean_text(best_by_field.get("surname", {}).get("raw_value")) or None
    address = clean_address_text(best_by_field.get("address", {}).get("raw_value")) or None

    dob = parse_document_date(
        best_by_field.get("dob", {}).get("raw_value"),
        best_by_field.get("dob", {}).get("normalized_date"),
    )
    expiration_date = parse_document_date(
        best_by_field.get("expiration_date", {}).get("raw_value"),
        best_by_field.get("expiration_date", {}).get("normalized_date"),
    )
    issue_date = parse_document_date(
        best_by_field.get("issue_date", {}).get("raw_value"),
        best_by_field.get("issue_date", {}).get("normalized_date"),
    )

    cleaned_values: dict[str, str | None] = {
        "dl_number": dl_number,
        "given_name": given_name,
        "surname": surname,
        "address": address,
        "dob": dob.isoformat() if dob else None,
        "expiration_date": expiration_date.isoformat() if expiration_date else None,
        "issue_date": issue_date.isoformat() if issue_date else None,
    }

    entities_with_boxes: list[dict[str, Any]] = []
    bounding_boxes: dict[str, list[dict[str, float]]] = {}
    confidences: list[float] = []

    for field_name in (
        "dl_number",
        "given_name",
        "surname",
        "address",
        "dob",
        "issue_date",
        "expiration_date",
    ):
        item = best_by_field.get(field_name)
        if not item:
            continue
        confidences.append(item["confidence"])
        bounding_boxes[field_name] = item["bounding_poly"]
        entities_with_boxes.append(
            {
                "field": field_name,
                "entity_type": item["entity_type"],
                "raw_value": item["raw_value"],
                "cleaned_value": cleaned_values.get(field_name),
                "confidence": item["confidence"],
                "page": item["page"],
                "bounding_poly": item["bounding_poly"],
            }
        )

    avg_confidence = (
        round(sum(confidences) / len(confidences), 4) if confidences else None
    )

    return {
        "dl_number": dl_number,
        "given_name": given_name,
        "surname": surname,
        "address": address,
        "dob": dob,
        "expiration_date": expiration_date,
        "issue_date": issue_date,
        "avg_confidence": avg_confidence,
        "bounding_boxes": bounding_boxes,
        "entities_with_boxes": entities_with_boxes,
        "raw_entities": all_raw_entities,
    }
