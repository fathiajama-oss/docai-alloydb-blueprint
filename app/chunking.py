# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Layout-aware chunk extraction and normalization for Document AI outputs."""

from __future__ import annotations

import re
from typing import Any


DEFAULT_FALLBACK_CHUNK_CHARS = 1200
DEFAULT_FALLBACK_OVERLAP_CHARS = 180


def clean_chunk_text(text: str | None) -> str:
    """Normalizes whitespace while preserving markdown tables, bullets, and numbers."""
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in normalized.split("\n")]
    joined = "\n".join(lines)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


def _infer_section_heading(
    chunk_obj: dict[str, Any],
    content: str,
    last_heading: str | None = None,
) -> str | None:
    """Derives the most descriptive section heading for a parsed chunk."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading[:180]

    headers = chunk_obj.get("pageHeaders") or chunk_obj.get("page_headers") or []
    for hdr in headers:
        if isinstance(hdr, dict):
            hdr_text = clean_chunk_text(str(hdr.get("text") or ""))
            if hdr_text and "SYNTHETIC ENTERPRISE SPECIMEN" not in hdr_text.upper():
                return hdr_text[:180]

    for line in content.splitlines():
        stripped = line.strip()
        if re.match(r"^(?:\d+(?:\.\d+)?)\s+[A-Za-z].{2,90}$", stripped):
            return stripped[:180]

    return last_heading


def _coerce_page_span(span_obj: dict[str, Any] | None, default_page: int = 1) -> tuple[int, int]:
    """Parses pageStart/pageEnd (camelCase or snake_case) into 1-indexed ints."""
    if not isinstance(span_obj, dict):
        return default_page, default_page
    raw_start = span_obj.get("pageStart")
    if raw_start is None:
        raw_start = span_obj.get("page_start")
    raw_end = span_obj.get("pageEnd")
    if raw_end is None:
        raw_end = span_obj.get("page_end")

    try:
        start = max(1, int(raw_start)) if raw_start is not None else default_page
    except (TypeError, ValueError):
        start = default_page

    try:
        end = max(start, int(raw_end)) if raw_end is not None else start
    except (TypeError, ValueError):
        end = start

    return start, end


def _render_table_block_markdown(table_block: dict[str, Any]) -> str:
    """Converts a DocumentLayout LayoutTableBlock into compact markdown table text."""
    def _row_cells(row: dict[str, Any]) -> list[str]:
        cells = row.get("cells") or []
        rendered: list[str] = []
        for cell in cells:
            blocks = cell.get("blocks") or []
            parts = []
            for blk in blocks:
                tb = blk.get("textBlock") or blk.get("text_block")
                if isinstance(tb, dict) and tb.get("text"):
                    parts.append(clean_chunk_text(str(tb["text"])))
            rendered.append(" ".join(parts).replace("|", "/"))
        return rendered

    header_rows = table_block.get("headerRows") or table_block.get("header_rows") or []
    body_rows = table_block.get("bodyRows") or table_block.get("body_rows") or []
    lines: list[str] = []

    if header_rows:
        hdr = _row_cells(header_rows[0])
        if hdr:
            lines.append("| " + " | ".join(hdr) + " |")
            lines.append("| " + " | ".join("---" for _ in hdr) + " |")

    for row in body_rows:
        cells = _row_cells(row)
        if cells:
            lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def _flatten_layout_blocks(
    blocks: list[dict[str, Any]],
    current_heading: str | None = None,
) -> list[dict[str, Any]]:
    """Recursively extracts ordered text/table/list entries from DocumentLayout blocks."""
    entries: list[dict[str, Any]] = []
    active_heading = current_heading

    for block in blocks:
        if not isinstance(block, dict):
            continue
        p_start, p_end = _coerce_page_span(block.get("pageSpan") or block.get("page_span"))

        text_block = block.get("textBlock") or block.get("text_block")
        if isinstance(text_block, dict):
            b_type = str(text_block.get("type") or text_block.get("type_") or "").lower()
            text = clean_chunk_text(str(text_block.get("text") or ""))
            if text:
                if "heading" in b_type:
                    active_heading = text[:180]
                    entries.append(
                        {
                            "text": f"## {text}",
                            "page_start": p_start,
                            "page_end": p_end,
                            "heading": active_heading,
                        }
                    )
                else:
                    entries.append(
                        {
                            "text": text,
                            "page_start": p_start,
                            "page_end": p_end,
                            "heading": active_heading,
                        }
                    )
            nested = text_block.get("blocks") or []
            if nested:
                entries.extend(_flatten_layout_blocks(nested, active_heading))

        table_block = block.get("tableBlock") or block.get("table_block")
        if isinstance(table_block, dict):
            md_table = _render_table_block_markdown(table_block)
            if md_table:
                entries.append(
                    {
                        "text": md_table,
                        "page_start": p_start,
                        "page_end": p_end,
                        "heading": active_heading,
                    }
                )

        list_block = block.get("listBlock") or block.get("list_block")
        if isinstance(list_block, dict):
            list_entries = list_block.get("listEntries") or list_block.get("list_entries") or []
            for entry in list_entries:
                sub_blocks = entry.get("blocks") or []
                entries.extend(_flatten_layout_blocks(sub_blocks, active_heading))

    return entries


def _split_text_with_overlap(
    text: str,
    max_chars: int = DEFAULT_FALLBACK_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_FALLBACK_OVERLAP_CHARS,
) -> list[str]:
    """Splits plain text into bounded overlapping slices at paragraph/sentence boundaries."""
    cleaned = clean_chunk_text(text)
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    n = len(cleaned)
    while start < n:
        end = min(n, start + max_chars)
        if end < n:
            para_break = cleaned.rfind("\n\n", start + max_chars // 2, end)
            line_break = cleaned.rfind("\n", start + max_chars // 2, end)
            space_break = cleaned.rfind(" ", start + max_chars // 2, end)
            split_at = max(para_break, line_break, space_break)
            if split_at > start:
                end = split_at
        slice_text = cleaned[start:end].strip()
        if slice_text:
            chunks.append(slice_text)
        if end >= n:
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _extract_page_texts(document_dict: dict[str, Any]) -> list[tuple[int, str]]:
    """Extracts per-page text from Document AI `pages` anchors when available."""
    full_text = str(document_dict.get("text") or "")
    pages = document_dict.get("pages") or []
    if not full_text or not pages:
        return []

    results: list[tuple[int, str]] = []
    for idx, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        layout = page.get("layout") or {}
        anchor = layout.get("textAnchor") or layout.get("text_anchor") or {}
        segments = anchor.get("textSegments") or anchor.get("text_segments") or []
        parts: list[str] = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            start_idx = int(seg.get("startIndex") or seg.get("start_index") or 0)
            end_idx = int(seg.get("endIndex") or seg.get("end_index") or 0)
            if end_idx > start_idx:
                parts.append(full_text[start_idx:end_idx])
        page_text = clean_chunk_text("\n".join(parts))
        if page_text:
            results.append((idx, page_text))
    return results


def extract_chunks_from_docai(document_dict: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """Extracts ordered, page-anchored chunks from a Document AI JSON dictionary.

    Returns `(page_count, chunks)` where each chunk contains:
    `chunk_index`, `page_start`, `page_end`, `section_heading`, `content`, `token_estimate`.
    """
    pages = document_dict.get("pages") or []
    max_page_end = max(1, len(pages))
    chunks: list[dict[str, Any]] = []

    # 1. Primary Path: Document AI Layout Parser ChunkedDocument
    chunked_doc = document_dict.get("chunkedDocument") or document_dict.get("chunked_document") or {}
    raw_chunks = chunked_doc.get("chunks") or []
    last_heading: str | None = None

    for raw_chunk in raw_chunks:
        if not isinstance(raw_chunk, dict):
            continue
        content = clean_chunk_text(str(raw_chunk.get("content") or ""))
        if not content:
            continue
        p_start, p_end = _coerce_page_span(
            raw_chunk.get("pageSpan") or raw_chunk.get("page_span"),
            default_page=1,
        )
        max_page_end = max(max_page_end, p_end)
        heading = _infer_section_heading(raw_chunk, content, last_heading=last_heading)
        if heading:
            last_heading = heading
        chunks.append(
            {
                "chunk_index": len(chunks),
                "page_start": p_start,
                "page_end": p_end,
                "section_heading": heading,
                "content": content,
                "token_estimate": max(1, len(content) // 4),
            }
        )

    if chunks:
        return max(len(pages), max_page_end, 1), chunks

    # 2. Secondary Path: DocumentLayout blocks (if Layout Parser ran without ChunkingConfig)
    doc_layout = document_dict.get("documentLayout") or document_dict.get("document_layout") or {}
    layout_blocks = doc_layout.get("blocks") or []
    if layout_blocks:
        entries = _flatten_layout_blocks(layout_blocks)
        buffer: list[str] = []
        buf_start = 1
        buf_end = 1
        buf_heading: str | None = None
        buf_len = 0

        for entry in entries:
            entry_text = entry["text"]
            max_page_end = max(max_page_end, entry["page_end"])
            if buffer and buf_len + len(entry_text) > DEFAULT_FALLBACK_CHUNK_CHARS:
                joined = clean_chunk_text("\n\n".join(buffer))
                if joined:
                    chunks.append(
                        {
                            "chunk_index": len(chunks),
                            "page_start": buf_start,
                            "page_end": buf_end,
                            "section_heading": buf_heading,
                            "content": joined,
                            "token_estimate": max(1, len(joined) // 4),
                        }
                    )
                buffer = []
                buf_len = 0
                buf_start = entry["page_start"]
                buf_heading = entry["heading"]

            if not buffer:
                buf_start = entry["page_start"]
                buf_heading = entry["heading"]
            buf_end = entry["page_end"]
            if entry["heading"] and not buf_heading:
                buf_heading = entry["heading"]
            buffer.append(entry_text)
            buf_len += len(entry_text)

        if buffer:
            joined = clean_chunk_text("\n\n".join(buffer))
            if joined:
                chunks.append(
                    {
                        "chunk_index": len(chunks),
                        "page_start": buf_start,
                        "page_end": buf_end,
                        "section_heading": buf_heading,
                        "content": joined,
                        "token_estimate": max(1, len(joined) // 4),
                    }
                )

    if chunks:
        return max(len(pages), max_page_end, 1), chunks

    # 3. Fallback Path: Page-anchored or full raw OCR text
    page_texts = _extract_page_texts(document_dict)
    if page_texts:
        for page_num, p_text in page_texts:
            max_page_end = max(max_page_end, page_num)
            for piece in _split_text_with_overlap(p_text):
                heading = _infer_section_heading({}, piece, last_heading=last_heading)
                if heading:
                    last_heading = heading
                chunks.append(
                    {
                        "chunk_index": len(chunks),
                        "page_start": page_num,
                        "page_end": page_num,
                        "section_heading": heading,
                        "content": piece,
                        "token_estimate": max(1, len(piece) // 4),
                    }
                )
    else:
        raw_text = str(document_dict.get("text") or "")
        for piece in _split_text_with_overlap(raw_text):
            heading = _infer_section_heading({}, piece, last_heading=last_heading)
            if heading:
                last_heading = heading
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "page_start": 1,
                    "page_end": max_page_end,
                    "section_heading": heading,
                    "content": piece,
                    "token_estimate": max(1, len(piece) // 4),
                }
            )

    return max(len(pages), max_page_end, 1), chunks
