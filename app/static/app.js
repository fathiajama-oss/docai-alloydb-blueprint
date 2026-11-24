// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0

const state = {
  samples: [],
  health: null,
  activeStatusFilter: "",
  activeSearch: "",
  activeResult: null,
  latestSqlProof: "-- Process or query records to inspect live SQL statements.",
  ddlSql: "",
};

const FIELD_LABELS = [
  { key: "dl_number", label: "DL Number" },
  { key: "given_name", label: "Given Name" },
  { key: "surname", label: "Surname" },
  { key: "address", label: "Address" },
  { key: "dob", label: "Date of Birth" },
  { key: "issue_date", label: "Issue Date" },
  { key: "expiration_date", label: "Expiration Date" },
];

const PIPELINE_STEPS = ["gcs_upload", "docai_parse", "gcs_json_write", "alloydb_write"];

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatStatusLabel(status) {
  if (status === "VALID") return "Valid";
  if (status === "EXPIRING_SOON") return "Expiring Soon";
  if (status === "EXPIRED") return "Expired";
  return status || "Unknown";
}

function formatConfidence(val) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "--";
  return `${(Number(val) * 100).toFixed(1)}%`;
}

function setPipelineNode(stepKey, status, metaText) {
  const node = document.querySelector(`.pipeline-node[data-step="${stepKey}"]`);
  const badge = document.getElementById(`badge-${stepKey}`);
  const meta = document.getElementById(`time-${stepKey}`);
  if (!node || !badge || !meta) return;

  node.classList.remove("is-running", "is-done", "is-error");
  if (status === "RUNNING") {
    node.classList.add("is-running");
  } else if (status === "DONE") {
    node.classList.add("is-done");
  } else if (status === "ERROR") {
    node.classList.add("is-error");
  }
  badge.textContent = status;
  if (metaText) meta.textContent = metaText;
}

function resetPipelineStrip() {
  setPipelineNode("gcs_upload", "IDLE", "Raw document upload");
  setPipelineNode("docai_parse", "IDLE", "Entity & box extraction");
  setPipelineNode("gcs_json_write", "IDLE", "Cloud Storage archival");
  setPipelineNode("alloydb_write", "IDLE", "Relational commit");
  document.getElementById("pipelineTotalTime").textContent = "Ready";
}

let pipelineAnimTimer = null;

function startPipelineAnimation() {
  stopPipelineAnimation();
  resetPipelineStrip();
  document.getElementById("pipelineTotalTime").textContent = "Executing live pipeline...";
  setPipelineNode("gcs_upload", "RUNNING", "Uploading to Cloud Storage...");

  let idx = 0;
  pipelineAnimTimer = setInterval(() => {
    if (idx < PIPELINE_STEPS.length - 1) {
      setPipelineNode(PIPELINE_STEPS[idx], "DONE", "Completed");
      idx += 1;
      setPipelineNode(PIPELINE_STEPS[idx], "RUNNING", "In progress...");
    }
  }, 550);
}

function stopPipelineAnimation() {
  if (pipelineAnimTimer) {
    clearInterval(pipelineAnimTimer);
    pipelineAnimTimer = null;
  }
}

function applyPipelineSuccessTimings(timings) {
  stopPipelineAnimation();
  setPipelineNode("gcs_upload", "DONE", `${timings.gcs_upload_ms ?? 0} ms`);
  setPipelineNode("docai_parse", "DONE", `${timings.docai_parse_ms ?? 0} ms`);
  setPipelineNode("gcs_json_write", "DONE", `${timings.gcs_json_write_ms ?? 0} ms`);
  setPipelineNode("alloydb_write", "DONE", `${timings.alloydb_write_ms ?? 0} ms`);
  document.getElementById("pipelineTotalTime").textContent = `Total: ${timings.total_ms ?? 0} ms`;
}

function applyPipelineErrorStage(stage) {
  stopPipelineAnimation();
  const stageMap = {
    config: "gcs_upload",
    gcs_upload: "gcs_upload",
    docai_parse: "docai_parse",
    gcs_download: "gcs_json_write",
    alloydb_persist: "alloydb_write",
  };
  const failedStep = stageMap[stage] || "gcs_upload";
  const failedIndex = PIPELINE_STEPS.indexOf(failedStep);

  PIPELINE_STEPS.forEach((step, idx) => {
    if (idx < failedIndex) {
      setPipelineNode(step, "DONE", "Completed");
    } else if (idx === failedIndex) {
      setPipelineNode(step, "ERROR", "Failed");
    } else {
      setPipelineNode(step, "IDLE", "Skipped");
    }
  });
  document.getElementById("pipelineTotalTime").textContent = "Pipeline halted";
}

function showErrorBanner(title, message) {
  document.getElementById("errorTitle").textContent = title;
  document.getElementById("errorMessage").textContent = message;
  document.getElementById("errorBanner").classList.remove("hidden");
}

function hideErrorBanner() {
  document.getElementById("errorBanner").classList.add("hidden");
}

function openProofDrawer(tabName = "sql") {
  document.getElementById("proofBackdrop").classList.remove("hidden");
  document.getElementById("proofDrawer").classList.remove("hidden");
  switchDrawerTab(tabName);
}

function closeProofDrawer() {
  document.getElementById("proofBackdrop").classList.add("hidden");
  document.getElementById("proofDrawer").classList.add("hidden");
}

function switchDrawerTab(tabName) {
  document.querySelectorAll(".drawer-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.id === `tab-${tabName}`);
  });
}

function updateProofDrawerContent() {
  document.getElementById("proofLatestSql").textContent = state.latestSqlProof;
  document.getElementById("proofDdlSql").textContent =
    state.ddlSql || "-- Schema DDL loaded from sql/01_init_schema.sql";

  if (state.activeResult) {
    document.getElementById("proofSourceUri").textContent =
      state.activeResult.source_gcs_uri || "N/A";
    document.getElementById("proofOutputUri").textContent =
      state.activeResult.output_gcs_uri || "N/A";
    document.getElementById("proofRawJson").textContent = JSON.stringify(
      state.activeResult.raw_docai_json || {},
      null,
      2
    );
  }
}

async function fetchHealth() {
  const pill = document.getElementById("healthPill");
  const label = document.getElementById("healthPillLabel");
  try {
    const resp = await fetch("/api/health");
    const data = await resp.json();
    state.health = data;
    if (data.ddl_sql) {
      state.ddlSql = data.ddl_sql;
      updateProofDrawerContent();
    }

    const comps = data.components || {};
    const statuses = Object.values(comps).map((c) => c.status);

    pill.classList.remove("status-checking", "status-ok", "status-warn", "status-error");
    if (data.ready) {
      pill.classList.add("status-ok");
      label.textContent = "All Services Connected";
    } else if (statuses.includes("unconfigured")) {
      pill.classList.add("status-warn");
      label.textContent = "Configuration Required";
    } else {
      pill.classList.add("status-error");
      label.textContent = "Service Attention Needed";
    }

    const initBtn = document.getElementById("initSchemaBtn");
    if (comps.alloydb && comps.alloydb.status === "ok" && !comps.alloydb.schema_initialized) {
      initBtn.classList.remove("hidden");
    } else {
      initBtn.classList.add("hidden");
    }

    renderHealthPanel(comps);
  } catch (err) {
    pill.classList.remove("status-checking", "status-ok", "status-warn");
    pill.classList.add("status-error");
    label.textContent = "Backend Unreachable";
  }
}

function renderHealthPanel(components) {
  const grid = document.getElementById("healthGrid");
  const entries = [
    { key: "cloud_storage", title: "Cloud Storage" },
    { key: "document_ai", title: "Document AI" },
    { key: "alloydb", title: "AlloyDB for PostgreSQL" },
  ];

  grid.innerHTML = entries
    .map(({ key, title }) => {
      const info = components[key] || { status: "error", detail: "No status reported." };
      const badgeClass =
        info.status === "ok"
          ? "compliance-VALID"
          : info.status === "unconfigured"
          ? "compliance-EXPIRING_SOON"
          : "compliance-EXPIRED";
      return `
        <div class="health-item">
          <div class="health-item-top">
            <span class="health-item-name">${escapeHtml(title)}</span>
            <span class="compliance-badge ${badgeClass}">${escapeHtml(info.status)}</span>
          </div>
          <div class="health-item-detail">${escapeHtml(info.detail || "")}</div>
        </div>
      `;
    })
    .join("");
}

async function fetchSamples() {
  const grid = document.getElementById("samplesGrid");
  try {
    const resp = await fetch("/api/samples");
    const data = await resp.json();
    state.samples = data.samples || [];

    grid.innerHTML = state.samples
      .map(
        (sample) => `
        <article class="card sample-card">
          <div class="sample-thumb-wrap">
            <img class="sample-thumb" src="${escapeHtml(sample.preview_url)}" alt="${escapeHtml(sample.title)}" loading="lazy" />
          </div>
          <div class="sample-body">
            <div class="sample-top-row">
              <span class="sample-state">${escapeHtml(sample.state)}</span>
              <span class="compliance-badge compliance-${escapeHtml(sample.expected_status)}">${escapeHtml(formatStatusLabel(sample.expected_status))}</span>
            </div>
            <div class="sample-title">${escapeHtml(sample.holder_name)}</div>
            <div class="sample-meta mono">ID ${escapeHtml(sample.dl_number)} &middot; ${escapeHtml(sample.expiration_label)}</div>
            <div class="sample-actions">
              <button class="btn btn-primary btn-sm process-sample-btn" data-filename="${escapeHtml(sample.filename)}" type="button">
                Process Document
              </button>
            </div>
          </div>
        </article>
      `
      )
      .join("");

    document.querySelectorAll(".process-sample-btn").forEach((btn) => {
      btn.addEventListener("click", () => processSample(btn.dataset.filename));
    });
  } catch (err) {
    grid.innerHTML = `<div class="card empty-state">Unable to load sample documents: ${escapeHtml(err.message)}</div>`;
  }
}

function highlightFieldAndBox(fieldName) {
  document.querySelectorAll(".field-item").forEach((el) => {
    el.classList.toggle("is-active", Boolean(fieldName) && el.dataset.field === fieldName);
  });
  document.querySelectorAll(".bbox-poly").forEach((el) => {
    el.classList.toggle("is-active", Boolean(fieldName) && el.dataset.field === fieldName);
  });
}

function renderVerificationWorkspace(result, previewObjectUrl = null) {
  state.activeResult = result;
  if (result.sql_proof) state.latestSqlProof = result.sql_proof;
  if (result.ddl_sql) state.ddlSql = result.ddl_sql;
  updateProofDrawerContent();

  document.getElementById("verificationEmpty").classList.add("hidden");
  document.getElementById("verificationWorkspace").classList.remove("hidden");
  document.getElementById("activeResultBadges").classList.remove("hidden");

  const rec = result.record || {};
  document.getElementById("viewerFilename").textContent = result.filename || "processed_document";
  document.getElementById("activeConfidenceBadge").textContent = `Confidence: ${formatConfidence(rec.avg_confidence)}`;

  const statusBadge = document.getElementById("activeStatusBadge");
  statusBadge.className = `compliance-badge compliance-${rec.compliance_status || "UNKNOWN"}`;
  statusBadge.textContent = formatStatusLabel(rec.compliance_status);

  const imgEl = document.getElementById("documentPreviewImg");
  const svgEl = document.getElementById("bboxOverlay");
  const pdfEl = document.getElementById("pdfPlaceholder");

  if (result.mime_type === "application/pdf") {
    imgEl.classList.add("hidden");
    svgEl.classList.add("hidden");
    pdfEl.classList.remove("hidden");
  } else {
    pdfEl.classList.add("hidden");
    imgEl.classList.remove("hidden");
    svgEl.classList.remove("hidden");
    imgEl.src = previewObjectUrl || `/api/samples/${encodeURIComponent(result.filename)}`;

    const entities = result.entities_with_boxes || [];
    svgEl.innerHTML = entities
      .filter((ent) => Array.isArray(ent.bounding_poly) && ent.bounding_poly.length >= 3)
      .map((ent) => {
        const points = ent.bounding_poly
          .map((pt) => `${Number(pt.x) || 0},${Number(pt.y) || 0}`)
          .join(" ");
        return `<polygon class="bbox-poly" data-field="${escapeHtml(ent.field)}" points="${points}"></polygon>`;
      })
      .join("");

    svgEl.querySelectorAll(".bbox-poly").forEach((poly) => {
      poly.addEventListener("mouseenter", () => highlightFieldAndBox(poly.dataset.field));
      poly.addEventListener("mouseleave", () => highlightFieldAndBox(null));
    });
  }

  const boxMap = new Map();
  (result.entities_with_boxes || []).forEach((item) => {
    boxMap.set(item.field, item);
  });

  document.getElementById("fieldsCountLabel").textContent = `${boxMap.size} mapped fields`;
  const listEl = document.getElementById("extractedFieldsList");
  listEl.innerHTML = FIELD_LABELS.map(({ key, label }) => {
    const ent = boxMap.get(key);
    const val = rec[key] ?? (ent ? ent.cleaned_value : null) ?? "Not detected";
    const conf = ent ? formatConfidence(ent.confidence) : "--";
    return `
      <div class="field-item" data-field="${escapeHtml(key)}">
        <div class="field-item-top">
          <span class="field-label">${escapeHtml(label)}</span>
          <span class="field-confidence mono">${escapeHtml(conf)}</span>
        </div>
        <div class="field-value">${escapeHtml(val)}</div>
      </div>
    `;
  }).join("");

  listEl.querySelectorAll(".field-item").forEach((item) => {
    item.addEventListener("mouseenter", () => highlightFieldAndBox(item.dataset.field));
    item.addEventListener("mouseleave", () => highlightFieldAndBox(null));
  });
}

function setSampleButtonsDisabled(disabled) {
  document.querySelectorAll(".process-sample-btn").forEach((btn) => {
    btn.disabled = disabled;
  });
  document.getElementById("browseFileBtn").disabled = disabled;
}

async function processSample(filename) {
  hideErrorBanner();
  setSampleButtonsDisabled(true);
  startPipelineAnimation();

  try {
    const resp = await fetch(`/api/process/sample/${encodeURIComponent(filename)}`, {
      method: "POST",
    });
    const data = await resp.json();
    if (!resp.ok) {
      const detail = data.detail || {};
      applyPipelineErrorStage(detail.stage || "config");
      showErrorBanner(
        `Pipeline Stopped (${detail.stage || "error"})`,
        detail.message || data.detail || "Unable to process sample document."
      );
      return;
    }

    applyPipelineSuccessTimings(data.timings || {});
    renderVerificationWorkspace(data, `/api/samples/${encodeURIComponent(filename)}`);
    await fetchRecords();
  } catch (err) {
    applyPipelineErrorStage("gcs_upload");
    showErrorBanner("Network Error", err.message);
  } finally {
    setSampleButtonsDisabled(false);
  }
}

async function processUpload(file) {
  if (!file) return;
  hideErrorBanner();
  setSampleButtonsDisabled(true);
  startPipelineAnimation();

  const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await fetch("/api/process/upload", {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) {
      const detail = data.detail || {};
      applyPipelineErrorStage(detail.stage || "config");
      showErrorBanner(
        `Pipeline Stopped (${detail.stage || "error"})`,
        detail.message || data.detail || "Unable to process uploaded document."
      );
      return;
    }

    applyPipelineSuccessTimings(data.timings || {});
    renderVerificationWorkspace(data, previewUrl);
    await fetchRecords();
  } catch (err) {
    applyPipelineErrorStage("gcs_upload");
    showErrorBanner("Upload Failed", err.message);
  } finally {
    setSampleButtonsDisabled(false);
  }
}

async function fetchRecords() {
  const noticeEl = document.getElementById("recordsStatusBanner");
  const listEl = document.getElementById("recordsList");
  const params = new URLSearchParams();
  if (state.activeStatusFilter) params.set("status", state.activeStatusFilter);
  if (state.activeSearch.trim()) params.set("search", state.activeSearch.trim());

  try {
    const resp = await fetch(`/api/records?${params.toString()}`);
    const data = await resp.json();

    if (!resp.ok) {
      const detail = data.detail || {};
      noticeEl.textContent = `AlloyDB ledger unavailable: ${detail.message || "Check connection or start AlloyDB Auth Proxy."}`;
      noticeEl.classList.remove("hidden");
      listEl.innerHTML = "";
      return;
    }

    noticeEl.classList.add("hidden");
    const summary = data.summary || {};
    document.getElementById("kpiTotal").textContent = summary.total ?? 0;
    document.getElementById("kpiValid").textContent = summary.valid ?? 0;
    document.getElementById("kpiExpiring").textContent = summary.expiring_soon ?? 0;
    document.getElementById("kpiExpired").textContent = summary.expired ?? 0;
    document.getElementById("kpiConfidence").textContent = formatConfidence(summary.avg_confidence);

    if (data.sql_proof && !state.activeResult) {
      state.latestSqlProof = data.sql_proof;
    }
    if (data.ddl_sql) {
      state.ddlSql = data.ddl_sql;
    }
    updateProofDrawerContent();

    const rows = data.records || [];
    if (rows.length === 0) {
      listEl.innerHTML = `
        <div class="card empty-state">
          <p class="empty-title">No matching records in AlloyDB</p>
          <p class="empty-copy">Process a driver's license above or adjust your search and filter criteria.</p>
        </div>
      `;
      return;
    }

    listEl.innerHTML = rows
      .map(
        (row) => `
        <article class="card record-row">
          <div>
            <div class="record-name">${escapeHtml(row.given_name || "")} ${escapeHtml(row.surname || "")}</div>
            <div class="record-sub">${escapeHtml(row.address || "Address unavailable")}</div>
          </div>
          <div>
            <div class="record-meta-label">License Number</div>
            <div class="record-meta-val mono">${escapeHtml(row.dl_number || "--")}</div>
          </div>
          <div>
            <div class="record-meta-label">Dates (DOB / ISS / EXP)</div>
            <div class="record-meta-val mono">${escapeHtml(row.dob || "--")} &middot; ${escapeHtml(row.issue_date || "--")} &middot; ${escapeHtml(row.expiration_date || "--")}</div>
          </div>
          <div>
            <span class="compliance-badge compliance-${escapeHtml(row.compliance_status)}">${escapeHtml(formatStatusLabel(row.compliance_status))}</span>
            <div class="record-sub mono">Conf: ${escapeHtml(formatConfidence(row.avg_confidence))}</div>
          </div>
          <div class="record-actions">
            <button class="btn btn-secondary btn-xs delete-record-btn" data-id="${escapeHtml(row.id)}" type="button">Delete</button>
          </div>
        </article>
      `
      )
      .join("");

    listEl.querySelectorAll(".delete-record-btn").forEach((btn) => {
      btn.addEventListener("click", () => deleteRecord(btn.dataset.id));
    });
  } catch (err) {
    noticeEl.textContent = `Unable to query AlloyDB records: ${err.message}`;
    noticeEl.classList.remove("hidden");
  }
}

async function deleteRecord(recordId) {
  try {
    const resp = await fetch(`/api/records/${encodeURIComponent(recordId)}`, {
      method: "DELETE",
    });
    if (resp.ok) {
      await fetchRecords();
    }
  } catch (err) {
    showErrorBanner("Delete Failed", err.message);
  }
}

async function initializeSchema() {
  try {
    const resp = await fetch("/api/schema/init", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) {
      showErrorBanner("Schema Initialization Failed", data.detail?.message || "Database error");
      return;
    }
    await fetchHealth();
    await fetchRecords();
  } catch (err) {
    showErrorBanner("Schema Initialization Failed", err.message);
  }
}

function wireEvents() {
  document.getElementById("healthPill").addEventListener("click", () => {
    document.getElementById("healthDrawerPanel").classList.toggle("hidden");
  });
  document.getElementById("closeHealthPanelBtn").addEventListener("click", () => {
    document.getElementById("healthDrawerPanel").classList.add("hidden");
  });
  document.getElementById("refreshHealthBtn").addEventListener("click", fetchHealth);
  document.getElementById("initSchemaBtn").addEventListener("click", initializeSchema);

  document.getElementById("openProofBtn").addEventListener("click", () => openProofDrawer("sql"));
  document.getElementById("closeProofBtn").addEventListener("click", closeProofDrawer);
  document.getElementById("proofBackdrop").addEventListener("click", closeProofDrawer);
  document.getElementById("errorSetupHelpBtn").addEventListener("click", () => openProofDrawer("setup"));
  document.getElementById("dismissErrorBtn").addEventListener("click", hideErrorBanner);

  document.querySelectorAll(".drawer-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchDrawerTab(btn.dataset.tab));
  });

  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const target = document.getElementById(btn.dataset.copyTarget);
      if (!target) return;
      await navigator.clipboard.writeText(target.textContent);
      const original = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.textContent = original;
      }, 1400);
    });
  });

  const fileInput = document.getElementById("fileInput");
  const dropZone = document.getElementById("dropZone");

  document.getElementById("browseFileBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    fileInput.click();
  });

  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });
  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("drag-over");
  });
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    const file = e.dataTransfer?.files?.[0];
    if (file) processUpload(file);
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file) processUpload(file);
    fileInput.value = "";
  });

  document.querySelectorAll(".filter-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".filter-pill").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      state.activeStatusFilter = pill.dataset.status || "";
      fetchRecords();
    });
  });

  let searchTimer = null;
  document.getElementById("recordsSearchInput").addEventListener("input", (e) => {
    state.activeSearch = e.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(fetchRecords, 220);
  });

  document.getElementById("refreshRecordsBtn").addEventListener("click", fetchRecords);
}

document.addEventListener("DOMContentLoaded", () => {
  wireEvents();
  fetchHealth();
  fetchSamples();
  fetchRecords();
});
