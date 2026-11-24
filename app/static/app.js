// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0

const state = {
  samples: [],
  documents: [],
  health: null,
  chatHistory: [],
  activeCitations: [],
  activeIngestion: null,
  latestSqlProof: "-- Ingest a PDF or ask a question to inspect live SQL statements.",
  ddlSql: "",
  busy: false,
};

const PIPELINE_STEPS = ["gcs_upload", "docai_parse", "embedding", "alloydb_write"];

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatInlineMarkdown(rawText) {
  let safe = escapeHtml(rawText);
  safe = safe.replace(/\*\*([^*]+)\*\*/g, (_, inner) => `<strong>${inner}</strong>`);
  safe = safe.replace(/`([^`]+)`/g, (_, code) => `<code class="mono">${code}</code>`);
  safe = safe.replace(
    /\[(\d+)\]/g,
    (_, num) => `<button type="button" class="citation-pill" data-citation="${num}">[${num}]</button>`
  );
  return safe;
}

function renderMarkdownAnswer(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) {
      i += 1;
      continue;
    }

    // Compact Markdown table detection
    if (line.startsWith("|") && i + 1 < lines.length && /^\|\s*[-:]+/.test(lines[i + 1].trim())) {
      const headerCells = line
        .split("|")
        .slice(1, -1)
        .map((c) => c.trim());
      i += 2;
      const bodyRows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const rowCells = lines[i]
          .trim()
          .split("|")
          .slice(1, -1)
          .map((c) => c.trim());
        bodyRows.push(rowCells);
        i += 1;
      }
      html.push(
        `<table class="chat-md-table"><thead><tr>${headerCells
          .map((h) => `<th>${formatInlineMarkdown(h)}</th>`)
          .join("")}</tr></thead><tbody>${bodyRows
          .map(
            (r) =>
              `<tr>${r.map((c) => `<td>${formatInlineMarkdown(c)}</td>`).join("")}</tr>`
          )
          .join("")}</tbody></table>`
      );
      continue;
    }

    // Bullet list
    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ""));
        i += 1;
      }
      html.push(`<ul>${items.map((it) => `<li>${formatInlineMarkdown(it)}</li>`).join("")}</ul>`);
      continue;
    }

    // Numbered list
    if (/^\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      html.push(`<ol>${items.map((it) => `<li>${formatInlineMarkdown(it)}</li>`).join("")}</ol>`);
      continue;
    }

    // Heading
    if (/^#{1,4}\s+/.test(line)) {
      const text = line.replace(/^#{1,4}\s+/, "");
      html.push(`<p><strong>${formatInlineMarkdown(text)}</strong></p>`);
      i += 1;
      continue;
    }

    // Paragraph block
    const paraLines = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith("|") &&
      !/^[-*]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !/^#{1,4}\s+/.test(lines[i].trim())
    ) {
      paraLines.push(lines[i].trim());
      i += 1;
    }
    html.push(`<p>${formatInlineMarkdown(paraLines.join(" "))}</p>`);
  }

  return html.join("");
}

function setPipelineNode(stepKey, status, metaText) {
  const node = document.querySelector(`.pipeline-node[data-step="${stepKey}"]`);
  const badge = document.getElementById(`badge-${stepKey}`);
  const meta = document.getElementById(`time-${stepKey}`);
  if (!node || !badge || !meta) return;

  node.classList.remove("is-running", "is-done", "is-error");
  if (status === "RUNNING") node.classList.add("is-running");
  else if (status === "DONE") node.classList.add("is-done");
  else if (status === "ERROR") node.classList.add("is-error");

  badge.textContent = status;
  if (metaText) meta.textContent = metaText;
}

function resetPipelineStrip() {
  setPipelineNode("gcs_upload", "IDLE", "Raw PDF staging");
  setPipelineNode("docai_parse", "IDLE", "Structure & table chunking");
  setPipelineNode("embedding", "IDLE", "Dense document embeddings");
  setPipelineNode("alloydb_write", "IDLE", "HNSW vector persistence");
  document.getElementById("pipelineTotalTime").textContent = "Ready";
}

let pipelineAnimTimer = null;

function startPipelineAnimation(label = "Executing live pipeline...") {
  stopPipelineAnimation();
  resetPipelineStrip();
  document.getElementById("pipelineTotalTime").textContent = label;
  setPipelineNode("gcs_upload", "RUNNING", "Uploading PDF to Cloud Storage...");

  let idx = 0;
  pipelineAnimTimer = setInterval(() => {
    if (idx < PIPELINE_STEPS.length - 1) {
      setPipelineNode(PIPELINE_STEPS[idx], "DONE", "Completed");
      idx += 1;
      setPipelineNode(PIPELINE_STEPS[idx], "RUNNING", "In progress...");
    }
  }, 650);
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
  setPipelineNode("embedding", "DONE", `${timings.embedding_ms ?? 0} ms`);
  setPipelineNode("alloydb_write", "DONE", `${timings.alloydb_write_ms ?? 0} ms`);
  document.getElementById("pipelineTotalTime").textContent = `Total: ${timings.total_ms ?? 0} ms`;
}

function applyPipelineErrorStage(stage) {
  stopPipelineAnimation();
  const stageMap = {
    config: "gcs_upload",
    gcs_upload: "gcs_upload",
    docai_parse: "docai_parse",
    gcs_download: "docai_parse",
    vertex_embed: "embedding",
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

  if (state.activeIngestion) {
    document.getElementById("proofSourceUri").textContent =
      state.activeIngestion.source_gcs_uri || "N/A";
    document.getElementById("proofOutputUri").textContent =
      state.activeIngestion.output_gcs_uri || "N/A";
    document.getElementById("proofRawJson").textContent = JSON.stringify(
      state.activeIngestion.raw_docai_json || {},
      null,
      2
    );
  }
}

function setControlsDisabled(disabled) {
  state.busy = disabled;
  document.querySelectorAll(".ingest-sample-btn").forEach((btn) => {
    btn.disabled = disabled;
  });
  document.getElementById("browseFileBtn").disabled = disabled;
  document.getElementById("ingestAllBtn").disabled = disabled;
  document.getElementById("askBtn").disabled = disabled;
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
    { key: "document_ai", title: "Document AI Layout Parser" },
    { key: "vertex_ai", title: "Vertex AI & Gemini" },
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
          <div class="sample-top-row">
            <span class="sample-category">${escapeHtml(sample.category)}</span>
            <span class="sample-pages mono">${escapeHtml(sample.pages)} pages</span>
          </div>
          <div class="sample-title">${escapeHtml(sample.title)}</div>
          <p class="sample-summary">${escapeHtml(sample.summary)}</p>
          <div class="sample-actions">
            <a class="btn btn-secondary btn-sm" href="${escapeHtml(sample.pdf_url)}" target="_blank" rel="noopener">Open PDF</a>
            <button class="btn btn-primary btn-sm ingest-sample-btn" data-filename="${escapeHtml(sample.filename)}" type="button">
              Ingest &amp; Vectorize
            </button>
          </div>
        </article>
      `
      )
      .join("");

    document.querySelectorAll(".ingest-sample-btn").forEach((btn) => {
      btn.addEventListener("click", () => ingestSample(btn.dataset.filename));
    });
  } catch (err) {
    grid.innerHTML = `<div class="card empty-state">Unable to load sample PDFs: ${escapeHtml(err.message)}</div>`;
  }
}

async function ingestSample(filename, preserveControls = false) {
  hideErrorBanner();
  if (!preserveControls) setControlsDisabled(true);
  startPipelineAnimation(`Ingesting ${filename}...`);

  try {
    const resp = await fetch(`/api/documents/sample/${encodeURIComponent(filename)}`, {
      method: "POST",
    });
    const data = await resp.json();
    if (!resp.ok) {
      const detail = data.detail || {};
      applyPipelineErrorStage(detail.stage || "config");
      showErrorBanner(
        `Ingestion Stopped (${detail.stage || "error"})`,
        detail.message || data.detail || "Unable to ingest sample PDF."
      );
      return false;
    }

    state.activeIngestion = data;
    if (data.sql_proof) state.latestSqlProof = data.sql_proof;
    if (data.ddl_sql) state.ddlSql = data.ddl_sql;
    updateProofDrawerContent();
    applyPipelineSuccessTimings(data.timings || {});
    await fetchDocuments();
    return true;
  } catch (err) {
    applyPipelineErrorStage("gcs_upload");
    showErrorBanner("Network Error", err.message);
    return false;
  } finally {
    if (!preserveControls) setControlsDisabled(false);
  }
}

async function ingestAllSamples() {
  if (!state.samples.length) return;
  hideErrorBanner();
  setControlsDisabled(true);
  try {
    for (const sample of state.samples) {
      const ok = await ingestSample(sample.filename, true);
      if (!ok) break;
    }
  } finally {
    setControlsDisabled(false);
  }
}

async function ingestUpload(file) {
  if (!file) return;
  hideErrorBanner();
  setControlsDisabled(true);
  startPipelineAnimation(`Ingesting ${file.name}...`);

  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await fetch("/api/documents/upload", {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) {
      const detail = data.detail || {};
      applyPipelineErrorStage(detail.stage || "config");
      showErrorBanner(
        `Upload Stopped (${detail.stage || "error"})`,
        detail.message || data.detail || "Unable to ingest uploaded PDF."
      );
      return;
    }

    state.activeIngestion = data;
    if (data.sql_proof) state.latestSqlProof = data.sql_proof;
    if (data.ddl_sql) state.ddlSql = data.ddl_sql;
    updateProofDrawerContent();
    applyPipelineSuccessTimings(data.timings || {});
    await fetchDocuments();
  } catch (err) {
    applyPipelineErrorStage("gcs_upload");
    showErrorBanner("Upload Failed", err.message);
  } finally {
    setControlsDisabled(false);
  }
}

function updateScopeDropdown(documents) {
  const select = document.getElementById("chatScopeSelect");
  const prevValue = select.value;
  select.innerHTML = `<option value="">All Indexed Documents</option>`;
  documents.forEach((doc) => {
    const opt = document.createElement("option");
    opt.value = String(doc.id);
    opt.textContent = `${doc.title} (${doc.page_count}p / ${doc.chunk_count} chunks)`;
    select.appendChild(opt);
  });
  if ([...select.options].some((o) => o.value === prevValue)) {
    select.value = prevValue;
  }
}

async function fetchDocuments() {
  const noticeEl = document.getElementById("documentsStatusBanner");
  const listEl = document.getElementById("documentsList");

  try {
    const resp = await fetch("/api/documents");
    const data = await resp.json();

    if (!resp.ok) {
      const detail = data.detail || {};
      noticeEl.textContent = `AlloyDB knowledge base unavailable: ${detail.message || "Check connection or start AlloyDB Auth Proxy."}`;
      noticeEl.classList.remove("hidden");
      listEl.innerHTML = "";
      return;
    }

    noticeEl.classList.add("hidden");
    const docs = data.documents || [];
    const summary = data.summary || {};
    state.documents = docs;

    document.getElementById("kpiDocs").textContent = summary.document_count ?? 0;
    document.getElementById("kpiChunks").textContent = summary.total_chunks ?? 0;
    document.getElementById("kpiPages").textContent = summary.total_pages ?? 0;

    updateScopeDropdown(docs);

    if (data.sql_proof && !state.activeIngestion && state.chatHistory.length === 0) {
      state.latestSqlProof = data.sql_proof;
    }
    if (data.ddl_sql) state.ddlSql = data.ddl_sql;
    updateProofDrawerContent();

    if (docs.length === 0) {
      listEl.innerHTML = `
        <div class="card empty-state">
          <p class="empty-title">No indexed PDFs in AlloyDB yet</p>
          <p class="empty-copy">Click Ingest &amp; Vectorize on a sample PDF above or upload your own PDF to populate the vector store.</p>
        </div>
      `;
      return;
    }

    const sampleSet = new Set(state.samples.map((s) => s.filename));
    listEl.innerHTML = docs
      .map(
        (doc) => `
        <article class="card doc-card" data-doc-id="${escapeHtml(doc.id)}">
          <div class="record-row">
            <div>
              <div class="record-name">${escapeHtml(doc.title)}</div>
              <div class="record-sub mono">${escapeHtml(doc.filename)} &middot; ${escapeHtml(doc.source_gcs_uri)}</div>
            </div>
            <div>
              <div class="record-meta-label">Pages &amp; Chunks</div>
              <div class="record-meta-val mono">${escapeHtml(doc.page_count)} pages &middot; ${escapeHtml(doc.chunk_count)} chunks</div>
            </div>
            <div>
              <div class="record-meta-label">Indexed At</div>
              <div class="record-meta-val mono">${escapeHtml(String(doc.processed_at || "").replace("T", " ").slice(0, 19))}</div>
            </div>
            <div class="record-actions">
              ${
                sampleSet.has(doc.filename)
                  ? `<a class="btn btn-secondary btn-xs" href="/api/samples/${encodeURIComponent(doc.filename)}" target="_blank" rel="noopener">Open PDF</a>`
                  : ""
              }
              <button class="btn btn-secondary btn-xs inspect-chunks-btn" data-id="${escapeHtml(doc.id)}" type="button">Inspect Chunks</button>
              <button class="btn btn-secondary btn-xs delete-doc-btn" data-id="${escapeHtml(doc.id)}" type="button">Delete</button>
            </div>
          </div>
          <div id="chunks-drawer-${escapeHtml(doc.id)}" class="chunks-drawer hidden"></div>
        </article>
      `
      )
      .join("");

    listEl.querySelectorAll(".inspect-chunks-btn").forEach((btn) => {
      btn.addEventListener("click", () => toggleDocumentChunks(btn.dataset.id, btn));
    });

    listEl.querySelectorAll(".delete-doc-btn").forEach((btn) => {
      btn.addEventListener("click", () => deleteDocumentRecord(btn.dataset.id));
    });
  } catch (err) {
    noticeEl.textContent = `Unable to query indexed documents: ${err.message}`;
    noticeEl.classList.remove("hidden");
  }
}

async function toggleDocumentChunks(docId, btn) {
  const drawer = document.getElementById(`chunks-drawer-${docId}`);
  if (!drawer) return;
  if (!drawer.classList.contains("hidden")) {
    drawer.classList.add("hidden");
    btn.textContent = "Inspect Chunks";
    return;
  }

  btn.disabled = true;
  btn.textContent = "Loading...";
  try {
    const resp = await fetch(`/api/documents/${encodeURIComponent(docId)}/chunks`);
    const data = await resp.json();
    if (!resp.ok) {
      showErrorBanner("Chunk Inspection Failed", data.detail?.message || "Unable to fetch chunks.");
      return;
    }
    const chunks = data.chunks || [];
    drawer.innerHTML = chunks
      .map(
        (ch) => `
        <div class="chunk-preview-item">
          <div class="chunk-preview-top">
            <strong>#${escapeHtml(ch.chunk_index + 1)} &middot; ${escapeHtml(ch.section_heading || "Document Section")}</strong>
            <span class="mono">Page ${escapeHtml(ch.page_start)}${ch.page_end !== ch.page_start ? `-${escapeHtml(ch.page_end)}` : ""} &middot; ${escapeHtml(ch.embedding_dims)}-d vector</span>
          </div>
          <pre class="chunk-preview-body">${escapeHtml(ch.content)}</pre>
        </div>
      `
      )
      .join("");
    drawer.classList.remove("hidden");
    btn.textContent = "Hide Chunks";
  } catch (err) {
    showErrorBanner("Chunk Inspection Failed", err.message);
  } finally {
    btn.disabled = false;
  }
}

async function deleteDocumentRecord(docId) {
  try {
    const resp = await fetch(`/api/documents/${encodeURIComponent(docId)}`, {
      method: "DELETE",
    });
    if (resp.ok) {
      await fetchDocuments();
    }
  } catch (err) {
    showErrorBanner("Delete Failed", err.message);
  }
}

function highlightEvidenceCard(citationIdx) {
  document.querySelectorAll(".evidence-card").forEach((card) => {
    const isMatch = Boolean(citationIdx) && Number(card.dataset.citation) === Number(citationIdx);
    card.classList.toggle("is-highlighted", isMatch);
    if (isMatch) {
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });
}

function renderEvidenceList(citations) {
  state.activeCitations = citations || [];
  const listEl = document.getElementById("evidenceList");
  const countEl = document.getElementById("evidenceCountPill");
  countEl.textContent = `${state.activeCitations.length} chunk${state.activeCitations.length === 1 ? "" : "s"}`;

  if (state.activeCitations.length === 0) {
    listEl.innerHTML = `<div class="empty-state-compact">No matching chunks retrieved from AlloyDB for this query.</div>`;
    return;
  }

  listEl.innerHTML = state.activeCitations
    .map((cit) => {
      const simPct = (Number(cit.vector_similarity || 0) * 100).toFixed(1);
      const pageLabel =
        cit.page_start === cit.page_end
          ? `Page ${cit.page_start}`
          : `Pages ${cit.page_start}-${cit.page_end}`;
      return `
        <article class="evidence-card" data-citation="${escapeHtml(cit.citation_index)}">
          <div class="evidence-card-top">
            <div class="evidence-title-wrap">
              <span class="evidence-idx mono">[${escapeHtml(cit.citation_index)}]</span>
              <span class="evidence-doc-title">${escapeHtml(cit.document_title)}</span>
            </div>
            <span class="sim-badge mono">${escapeHtml(simPct)}% Cosine Match</span>
          </div>
          <div class="evidence-meta mono">
            ${escapeHtml(pageLabel)} &middot; ${escapeHtml(cit.section_heading || "General Section")} &middot; Hybrid ${escapeHtml(Number(cit.hybrid_score || 0).toFixed(3))}
          </div>
          <pre class="evidence-content">${escapeHtml(cit.content)}</pre>
        </article>
      `;
    })
    .join("");
}

function renderChatMessages() {
  const container = document.getElementById("chatMessages");
  if (state.chatHistory.length === 0) {
    container.innerHTML = `
      <div class="chat-empty">
        <p class="chat-empty-title">Ready for grounded document Q&amp;A</p>
        <p class="chat-empty-copy">
          Ingest at least one PDF in Step 01, then click a starter prompt above or ask your own question below.
        </p>
      </div>
    `;
    return;
  }

  container.innerHTML = state.chatHistory
    .map((turn) => {
      if (turn.role === "user") {
        return `<div class="chat-bubble chat-bubble-user">${escapeHtml(turn.content)}</div>`;
      }
      const t = turn.timings || {};
      const latencyNote = t.alloydb_search_ms !== undefined
        ? `Vector SQL: ${t.alloydb_search_ms} ms &middot; Gemini: ${t.gemini_generate_ms} ms`
        : "";
      return `
        <div class="chat-bubble chat-bubble-assistant">
          <div class="chat-answer-body">${renderMarkdownAnswer(turn.content)}</div>
          ${
            latencyNote
              ? `<div class="chat-bubble-meta mono"><span>Grounded in ${escapeHtml((turn.citations || []).length)} AlloyDB chunk(s)</span><span>${latencyNote}</span></div>`
              : ""
          }
        </div>
      `;
    })
    .join("");

  container.querySelectorAll(".citation-pill").forEach((pill) => {
    pill.addEventListener("mouseenter", () => highlightEvidenceCard(pill.dataset.citation));
    pill.addEventListener("click", () => highlightEvidenceCard(pill.dataset.citation));
  });

  container.scrollTop = container.scrollHeight;
}

async function submitChatQuestion(rawQuestion) {
  const question = String(rawQuestion || "").trim();
  if (!question || state.busy) return;

  hideErrorBanner();
  const scopeVal = document.getElementById("chatScopeSelect").value;
  const documentId = scopeVal ? Number(scopeVal) : null;

  const priorTurns = state.chatHistory.map((t) => ({
    role: t.role,
    content: t.content,
  }));

  state.chatHistory.push({ role: "user", content: question });
  renderChatMessages();
  document.getElementById("chatInput").value = "";
  setControlsDisabled(true);

  const askBtn = document.getElementById("askBtn");
  const originalLabel = askBtn.textContent;
  askBtn.textContent = "Searching...";

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        document_id: documentId,
        top_k: 5,
        history: priorTurns,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      const detail = data.detail || {};
      showErrorBanner(
        `Chat Request Failed (${detail.stage || "error"})`,
        detail.message || data.detail || "Unable to query AlloyDB or Gemini."
      );
      state.chatHistory.pop();
      renderChatMessages();
      return;
    }

    if (data.sql_proof) state.latestSqlProof = data.sql_proof;
    if (data.ddl_sql) state.ddlSql = data.ddl_sql;
    updateProofDrawerContent();

    state.chatHistory.push({
      role: "assistant",
      content: data.answer,
      citations: data.citations || [],
      timings: data.timings || {},
    });
    renderChatMessages();
    renderEvidenceList(data.citations || []);
  } catch (err) {
    showErrorBanner("Chat Request Failed", err.message);
    state.chatHistory.pop();
    renderChatMessages();
  } finally {
    askBtn.textContent = originalLabel;
    setControlsDisabled(false);
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
    await fetchDocuments();
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
  document.getElementById("ingestAllBtn").addEventListener("click", ingestAllSamples);

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
    if (file) ingestUpload(file);
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file) ingestUpload(file);
    fileInput.value = "";
  });

  document.querySelectorAll(".prompt-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      submitChatQuestion(chip.dataset.prompt);
    });
  });

  document.getElementById("chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    submitChatQuestion(document.getElementById("chatInput").value);
  });

  document.getElementById("clearChatBtn").addEventListener("click", () => {
    state.chatHistory = [];
    renderChatMessages();
    renderEvidenceList([]);
  });

  document.getElementById("refreshDocumentsBtn").addEventListener("click", fetchDocuments);
}

document.addEventListener("DOMContentLoaded", () => {
  wireEvents();
  fetchHealth();
  fetchSamples();
  fetchDocuments();
});
