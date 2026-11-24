# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Real Vertex AI Embeddings and Gemini grounded generation via google-genai."""

from __future__ import annotations

import time
from typing import Any

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google import genai
from google.genai import types

from app.config import Settings


EMBEDDING_BATCH_SIZE = 50
MAX_HISTORY_TURNS = 6

GROUNDED_SYSTEM_INSTRUCTION = (
    "You are an enterprise technical document assistant backed by AlloyDB for PostgreSQL "
    "and Document AI Layout Parser. Answer questions accurately and concisely using ONLY "
    "the retrieved document chunks provided in the prompt.\n"
    "- Always cite supporting evidence inline using bracketed citation numbers such as [1] or [1][2] "
    "immediately after the relevant claim.\n"
    "- Present quantitative thresholds, operational limits, or SLA matrices cleanly using bullet points "
    "or compact markdown tables when helpful.\n"
    "- If the retrieved chunks do not contain the answer, clearly state that the indexed PDFs do not "
    "cover that topic rather than guessing or inventing figures."
)


class RagServiceError(RuntimeError):
    """Raised when Vertex AI embedding or Gemini generation fails."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message


def check_vertex_health(settings: Settings) -> dict[str, Any]:
    """Verifies Vertex AI project configuration, ADC availability, and client setup."""
    if not settings.is_vertex_configured():
        return {
            "status": "unconfigured",
            "detail": "Set GCP_PROJECT_ID in .env to enable Vertex AI Embeddings and Gemini.",
        }
    try:
        creds, _ = google.auth.default()
        if creds is None:
            return {
                "status": "error",
                "detail": "No Application Default Credentials found. Run gcloud auth application-default login.",
            }
        # Verify client instantiation for both embedding and chat locations
        genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location=settings.VERTEX_EMBEDDING_LOCATION,
        )
        genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location=settings.VERTEX_CHAT_LOCATION,
        )
        return {
            "status": "ok",
            "embedding_model": settings.VERTEX_EMBEDDING_MODEL,
            "embedding_location": settings.VERTEX_EMBEDDING_LOCATION,
            "chat_model": settings.VERTEX_CHAT_MODEL,
            "chat_location": settings.VERTEX_CHAT_LOCATION,
            "detail": (
                f"Vertex AI ready ({settings.VERTEX_EMBEDDING_MODEL} @ {settings.VERTEX_EMBEDDING_LOCATION}, "
                f"{settings.VERTEX_CHAT_MODEL} @ {settings.VERTEX_CHAT_LOCATION})."
            ),
        }
    except (DefaultCredentialsError, Exception) as exc:
        return {"status": "error", "detail": str(exc)}


def embed_texts(
    texts: list[str],
    settings: Settings,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """Generates normalized float vectors via Vertex AI Embeddings in batches."""
    if not texts:
        return []
    if not settings.is_vertex_configured():
        raise RagServiceError(
            "vertex_embed",
            "Missing GCP_PROJECT_ID in .env for Vertex AI Embeddings.",
        )

    try:
        client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location=settings.VERTEX_EMBEDDING_LOCATION,
        )
        all_vectors: list[list[float]] = []
        for idx in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[idx : idx + EMBEDDING_BATCH_SIZE]
            resp = client.models.embed_content(
                model=settings.VERTEX_EMBEDDING_MODEL,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=settings.EMBEDDING_DIMENSIONS,
                ),
            )
            embeddings = resp.embeddings or []
            if len(embeddings) != len(batch):
                raise RagServiceError(
                    "vertex_embed",
                    f"Expected {len(batch)} embeddings, received {len(embeddings)}.",
                )
            for item in embeddings:
                vec = [float(v) for v in (item.values or [])]
                if len(vec) != settings.EMBEDDING_DIMENSIONS:
                    raise RagServiceError(
                        "vertex_embed",
                        f"Expected {settings.EMBEDDING_DIMENSIONS}-d vector, got {len(vec)}.",
                    )
                all_vectors.append(vec)
        return all_vectors
    except RagServiceError:
        raise
    except Exception as exc:
        raise RagServiceError("vertex_embed", f"Vertex AI embedding failed: {exc}") from exc


def _format_context_blocks(retrieved_chunks: list[dict[str, Any]]) -> str:
    """Formats retrieved AlloyDB chunks into numbered citation blocks."""
    blocks: list[str] = []
    for idx, chunk in enumerate(retrieved_chunks, start=1):
        title = chunk.get("document_title") or chunk.get("filename") or "Document"
        p_start = chunk.get("page_start", 1)
        p_end = chunk.get("page_end", p_start)
        page_label = f"{p_start}" if p_start == p_end else f"{p_start}-{p_end}"
        heading = chunk.get("section_heading") or "General Section"
        content = chunk.get("content") or ""
        blocks.append(
            f"[{idx}] Document: {title} | Pages: {page_label} | Section: {heading}\n"
            f"Content:\n{content}"
        )
    return "\n\n---\n\n".join(blocks)


def generate_grounded_answer(
    question: str,
    retrieved_chunks: list[dict[str, Any]],
    history: list[dict[str, str]] | None,
    settings: Settings,
) -> dict[str, Any]:
    """Generates a citation-grounded response using Gemini on Vertex AI."""
    if not settings.is_vertex_configured():
        raise RagServiceError(
            "vertex_generate",
            "Missing GCP_PROJECT_ID in .env for Gemini generation.",
        )

    t0 = time.perf_counter()
    contents: list[types.Content] = []
    recent_history = (history or [])[-MAX_HISTORY_TURNS:]
    for turn in recent_history:
        raw_role = (turn.get("role") or "user").strip().lower()
        text = (turn.get("content") or "").strip()
        if not text:
            continue
        genai_role = "model" if raw_role in {"assistant", "model"} else "user"
        contents.append(
            types.Content(
                role=genai_role,
                parts=[types.Part.from_text(text=text)],
            )
        )

    context_str = _format_context_blocks(retrieved_chunks)
    prompt_text = (
        f"Retrieved Evidence from AlloyDB for PostgreSQL:\n\n"
        f"{context_str}\n\n"
        f"User Question:\n{question.strip()}"
    )
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt_text)],
        )
    )

    try:
        client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location=settings.VERTEX_CHAT_LOCATION,
        )
        response = client.models.generate_content(
            model=settings.VERTEX_CHAT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=GROUNDED_SYSTEM_INSTRUCTION,
                temperature=0.1,
            ),
        )
        answer_text = (response.text or "").strip()
        if not answer_text:
            raise RagServiceError(
                "vertex_generate",
                "Gemini returned an empty response.",
            )
    except RagServiceError:
        raise
    except Exception as exc:
        raise RagServiceError("vertex_generate", f"Gemini generation failed: {exc}") from exc

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "answer": answer_text,
        "latency_ms": latency_ms,
    }
