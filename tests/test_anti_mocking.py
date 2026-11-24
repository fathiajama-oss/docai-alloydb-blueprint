# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Static anti-mocking, license header, and open-source IP hygiene checks."""

from __future__ import annotations

import ast
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", ".git"}

HEADER_EXTENSIONS = {".py", ".sh", ".sql", ".js", ".css"}
HYGIENE_TEXT_EXTENSIONS = {
    ".py",
    ".sh",
    ".sql",
    ".js",
    ".css",
    ".html",
    ".md",
    ".example",
    ".txt",
}

FORBIDDEN_MOCK_TOKENS = (
    "MagicMock",
    "AsyncMock",
    "unittest.mock",
    "mock_response",
    "fake_data",
    "simulate_",
)

INTERNAL_PATTERNS = (
    re.compile(r"@" + r"google\.com", re.IGNORECASE),
    re.compile(r"corp\." + r"google\.com", re.IGNORECASE),
    re.compile(r"googleplex" + r"\.com", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_/:])" + r"go/[a-z0-9][a-z0-9_\-/]*", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_/:])" + r"b/[0-9]{5,}"),
    re.compile(r"(?<![A-Za-z0-9_/:])" + r"cl/[0-9]{5,}"),
)

CURRENCY_PRICING_PATTERN = re.compile(r"(?:\$\s*\d|\£|/mo\b)")


def _iter_repo_files(
    extensions: set[str],
    include_special: bool = False,
) -> list[Path]:
    """Iterates repository files excluding virtualenvs and caches."""
    files: list[Path] = []
    special_names = {"Dockerfile", ".gitignore", ".env.example"}
    for path in sorted(REPO_ROOT.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in extensions or (include_special and path.name in special_names):
            files.append(path)
    return files


def test_license_and_gitignore_present() -> None:
    """Ensures Apache 2.0 LICENSE and strict .gitignore entries exist."""
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text

    gitignore_text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", ".venv/", "__pycache__/", ".pytest_cache/", "*.pyc"):
        assert pattern in gitignore_text


def test_spdx_headers_on_all_source_files() -> None:
    """Verifies Copyright and Apache-2.0 SPDX headers on every code/style/SQL file."""
    missing: list[str] = []
    for path in _iter_repo_files(HEADER_EXTENSIONS, include_special=False):
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:5])
        if "Copyright 2026 Google LLC" not in head or "SPDX-License-Identifier: Apache-2.0" not in head:
            missing.append(str(path.relative_to(REPO_ROOT)))
    assert not missing, f"Missing Apache-2.0 header in: {missing}"


def test_zero_mocking_in_app_and_scripts() -> None:
    """AST and token scan enforcing zero mocks or fake fallback data in app/ and scripts/."""
    target_dirs = (REPO_ROOT / "app", REPO_ROOT / "scripts")
    violations: list[str] = []

    for base_dir in target_dirs:
        for py_file in sorted(base_dir.rglob("*.py")):
            source = py_file.read_text(encoding="utf-8")
            rel = str(py_file.relative_to(REPO_ROOT))

            for token in FORBIDDEN_MOCK_TOKENS:
                if token in source:
                    violations.append(f"{rel}: forbidden token '{token}'")

            tree = ast.parse(source, filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "mock" in alias.name.lower():
                            violations.append(f"{rel}: forbidden import '{alias.name}'")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if "mock" in module.lower():
                        violations.append(f"{rel}: forbidden from-import '{module}'")

    assert not violations, f"Zero-mocking violations detected: {violations}"


def test_real_gcp_and_db_sdk_imports_present() -> None:
    """Verifies real Cloud Storage, Document AI, and psycopg2 imports exist."""
    docai_src = (REPO_ROOT / "app" / "docai_service.py").read_text(encoding="utf-8")
    assert "from google.cloud import documentai_v1" in docai_src
    assert "from google.cloud import storage" in docai_src

    db_src = (REPO_ROOT / "app" / "db.py").read_text(encoding="utf-8")
    assert "import psycopg2" in db_src


def test_open_source_ip_and_font_hygiene() -> None:
    """Checks for internal links, internal emails, proprietary fonts, or currency symbols."""
    violations: list[str] = []

    for path in _iter_repo_files(HYGIENE_TEXT_EXTENSIONS, include_special=True):
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")

        for pattern in INTERNAL_PATTERNS:
            if pattern.search(text):
                violations.append(f"{rel}: matched internal pattern {pattern.pattern}")

        if path.suffix.lower() in {".html", ".css", ".js"}:
            if "google sans" in text.lower():
                violations.append(f"{rel}: proprietary font Google Sans referenced")

        if path.suffix.lower() in {".html", ".css", ".md"}:
            for literal in ("$", "£", "/mo"):
                if literal in text:
                    violations.append(f"{rel}: forbidden pricing/currency token '{literal}'")
        elif path.suffix.lower() == ".js":
            if CURRENCY_PRICING_PATTERN.search(text):
                violations.append(f"{rel}: forbidden pricing/currency pattern in JS")

    assert not violations, f"Open-source hygiene violations: {violations}"
