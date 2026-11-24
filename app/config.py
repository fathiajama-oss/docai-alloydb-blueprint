# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Application configuration loaded from environment variables and .env."""

from __future__ import annotations

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DOCS_DIR = BASE_DIR / "sample_docs"
SQL_DIR = BASE_DIR / "sql"
STATIC_DIR = Path(__file__).resolve().parent / "static"

_PLACEHOLDER_PREFIXES = ("<YOUR_", "your-docai-", "your-alloydb-")


def is_placeholder_value(value: str | None) -> bool:
    """Returns True when a setting is unset or still uses an example placeholder."""
    if not value or not value.strip():
        return True
    stripped = value.strip()
    return any(stripped.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


class Settings(BaseSettings):
    """Typed runtime settings for Cloud Storage, Document AI, and AlloyDB."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    GCP_PROJECT_ID: str = Field(default="")
    DOCAI_LOCATION: str = Field(default="us")
    DOCAI_PROCESSOR_ID: str = Field(default="")

    GCS_INPUT_BUCKET: str = Field(default="")
    GCS_OUTPUT_BUCKET: str = Field(default="")

    DB_HOST: str = Field(default="127.0.0.1")
    DB_PORT: int = Field(default=5432)
    DB_NAME: str = Field(default="postgres")
    DB_USER: str = Field(default="postgres")
    DB_PASSWORD: str = Field(default="")
    DB_CONNECT_TIMEOUT: int = Field(default=4)

    def missing_gcp_fields(self) -> list[str]:
        """Lists required Cloud Storage / Document AI settings that are missing."""
        required = {
            "GCP_PROJECT_ID": self.GCP_PROJECT_ID,
            "DOCAI_LOCATION": self.DOCAI_LOCATION,
            "DOCAI_PROCESSOR_ID": self.DOCAI_PROCESSOR_ID,
            "GCS_INPUT_BUCKET": self.GCS_INPUT_BUCKET,
            "GCS_OUTPUT_BUCKET": self.GCS_OUTPUT_BUCKET,
        }
        return [key for key, val in required.items() if is_placeholder_value(val)]

    def is_gcs_configured(self) -> bool:
        """Returns True if Cloud Storage bucket settings and project ID are set."""
        return not any(
            is_placeholder_value(v)
            for v in (self.GCP_PROJECT_ID, self.GCS_INPUT_BUCKET, self.GCS_OUTPUT_BUCKET)
        )

    def is_docai_configured(self) -> bool:
        """Returns True if Document AI project, location, and processor ID are set."""
        return not any(
            is_placeholder_value(v)
            for v in (self.GCP_PROJECT_ID, self.DOCAI_LOCATION, self.DOCAI_PROCESSOR_ID)
        )

    def is_db_configured(self) -> bool:
        """Returns True if AlloyDB host, port, database, and user are configured."""
        return bool(self.DB_HOST.strip() and self.DB_NAME.strip() and self.DB_USER.strip())


def get_settings() -> Settings:
    """Loads settings from environment variables and the repository .env file."""
    return Settings()
