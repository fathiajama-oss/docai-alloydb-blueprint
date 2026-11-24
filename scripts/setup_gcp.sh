#!/usr/bin/env bash
# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

GCP_PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [[ -z "${GCP_PROJECT_ID}" || "${GCP_PROJECT_ID}" == "(unset)" ]]; then
  echo "Error: GCP_PROJECT_ID is not set and no active gcloud project was found." >&2
  echo "Usage: GCP_PROJECT_ID=<your-project-id> bash scripts/setup_gcp.sh" >&2
  exit 1
fi

DOCAI_LOCATION="${DOCAI_LOCATION:-us}"
GCS_INPUT_BUCKET="${GCS_INPUT_BUCKET:-${GCP_PROJECT_ID}-docai-input}"
GCS_OUTPUT_BUCKET="${GCS_OUTPUT_BUCKET:-${GCP_PROJECT_ID}-docai-output}"
PROCESSOR_DISPLAY_NAME="${PROCESSOR_DISPLAY_NAME:-docai-layout-parser}"

echo "==> Configuring Google Cloud project: ${GCP_PROJECT_ID}"
echo "==> Enabling required APIs (Document AI, AlloyDB, Cloud Storage, Vertex AI)..."
gcloud services enable \
  documentai.googleapis.com \
  alloydb.googleapis.com \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  --project="${GCP_PROJECT_ID}"

ensure_bucket() {
  local bucket="$1"
  if gcloud storage buckets describe "gs://${bucket}" --project="${GCP_PROJECT_ID}" >/dev/null 2>&1; then
    echo "==> Cloud Storage bucket already exists: gs://${bucket}"
  else
    echo "==> Creating Cloud Storage bucket: gs://${bucket}"
    gcloud storage buckets create "gs://${bucket}" \
      --project="${GCP_PROJECT_ID}" \
      --location="${DOCAI_LOCATION}" \
      --uniform-bucket-level-access
  fi
}

ensure_bucket "${GCS_INPUT_BUCKET}"
ensure_bucket "${GCS_OUTPUT_BUCKET}"

echo "==> Checking for existing LAYOUT_PARSER_PROCESSOR in ${DOCAI_LOCATION}..."
ACCESS_TOKEN="$(gcloud auth print-access-token)"
API_BASE="https://${DOCAI_LOCATION}-documentai.googleapis.com/v1/projects/${GCP_PROJECT_ID}/locations/${DOCAI_LOCATION}/processors"

PROCESSORS_JSON="$(curl -fsSL \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "x-goog-user-project: ${GCP_PROJECT_ID}" \
  "${API_BASE}")"

DOCAI_PROCESSOR_ID="$(
  python3 -c '
import json, sys
data = json.load(sys.stdin)
for proc in data.get("processors", []):
    if proc.get("type") == "LAYOUT_PARSER_PROCESSOR":
        print(proc["name"].split("/")[-1])
        break
' <<< "${PROCESSORS_JSON}"
)"

if [[ -z "${DOCAI_PROCESSOR_ID}" ]]; then
  echo "==> Creating LAYOUT_PARSER_PROCESSOR (${PROCESSOR_DISPLAY_NAME})..."
  CREATED_JSON="$(curl -fsSL -X POST \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -H "x-goog-user-project: ${GCP_PROJECT_ID}" \
    -d "{\"displayName\":\"${PROCESSOR_DISPLAY_NAME}\",\"type\":\"LAYOUT_PARSER_PROCESSOR\"}" \
    "${API_BASE}")"
  DOCAI_PROCESSOR_ID="$(
    python3 -c '
import json, sys
proc = json.load(sys.stdin)
print(proc["name"].split("/")[-1])
' <<< "${CREATED_JSON}"
  )"
  echo "==> Created Document AI Layout Parser processor ID: ${DOCAI_PROCESSOR_ID}"
else
  echo "==> Reusing existing Document AI Layout Parser processor ID: ${DOCAI_PROCESSOR_ID}"
fi

PROXY_BIN="${REPO_ROOT}/alloydb-auth-proxy"
if [[ ! -x "${PROXY_BIN}" ]]; then
  OS_NAME="$(uname -s | tr '[:upper:]' '[:lower:]')"
  ARCH_RAW="$(uname -m)"
  case "${ARCH_RAW}" in
    x86_64|amd64) ARCH_NAME="amd64" ;;
    arm64|aarch64) ARCH_NAME="arm64" ;;
    *) ARCH_NAME="amd64" ;;
  esac
  PROXY_URL="https://storage.googleapis.com/alloydb-auth-proxy/latest/alloydb-auth-proxy.${OS_NAME}.${ARCH_NAME}"
  echo "==> Downloading AlloyDB Auth Proxy from ${PROXY_URL}..."
  curl -fsSL -o "${PROXY_BIN}" "${PROXY_URL}"
  chmod +x "${PROXY_BIN}"
else
  echo "==> AlloyDB Auth Proxy binary already present at ${PROXY_BIN}"
fi

cat <<EOF

====================================================================
Setup complete. Add the following configuration to ${REPO_ROOT}/.env:
====================================================================
GCP_PROJECT_ID=${GCP_PROJECT_ID}
DOCAI_LOCATION=${DOCAI_LOCATION}
DOCAI_PROCESSOR_ID=${DOCAI_PROCESSOR_ID}
GCS_INPUT_BUCKET=${GCS_INPUT_BUCKET}
GCS_OUTPUT_BUCKET=${GCS_OUTPUT_BUCKET}
VERTEX_EMBEDDING_LOCATION=us-central1
VERTEX_EMBEDDING_MODEL=text-embedding-005
VERTEX_CHAT_LOCATION=global
VERTEX_CHAT_MODEL=gemini-3-flash-preview
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=<YOUR_ALLOYDB_PASSWORD>
EOF
