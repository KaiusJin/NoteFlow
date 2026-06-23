const API_BASE_URL = localStorage.getItem("noteflowApiBaseUrl") || "http://localhost:8080";

const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#pdf-file");
const fileLabel = document.querySelector("#file-label");
const statusCard = document.querySelector("#status-card");
const progressBar = document.querySelector("#progress-bar");
const documentsList = document.querySelector("#documents-list");
const refreshDocuments = document.querySelector("#refresh-documents");
const openSearchButton = document.querySelector("#open-search");
const parseOutput = document.querySelector("#parse-output");

let documentsMap = new Map();
let latestTasksList = [];
const pendingNotesTasks = new Map(); // taskId -> documentId
let globalPollInterval = null;

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  fileLabel.textContent = file ? file.name : "Choose a PDF";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) {
    renderStatus("Choose a PDF first.", 0);
    return;
  }

  const submitButton = form.querySelector("button");
  submitButton.disabled = true;

  try {
    const data = new FormData();
    data.append("file", file);
    data.append("documentType", document.querySelector("#document-type").value);
    data.append("title", document.querySelector("#title").value);

    renderStatus("Uploading PDF...", 0);
    const response = await fetch(`${API_BASE_URL}/documents`, {
      method: "POST",
      body: data,
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.message || "Upload failed");
    }

    renderStatus(`Created document ${payload.documentId}\nCreated task ${payload.taskId}`, 5);
    await loadDocuments();
  } catch (error) {
    renderStatus(formatFetchError(error), 0);
  } finally {
    submitButton.disabled = false;
  }
});

refreshDocuments.addEventListener("click", loadDocuments);
openSearchButton.addEventListener("click", () => renderSearchPanel());

documentsList.addEventListener("click", async (event) => {
  const parseButton = event.target.closest("[data-view-parse]");
  if (parseButton) {
    await loadParsedOutput(parseButton.dataset.viewParse);
    return;
  }
  const notesButton = event.target.closest("[data-generate-notes]");
  if (notesButton) {
    await generateNotes(notesButton.dataset.generateNotes);
    return;
  }
  const viewNotesButton = event.target.closest("[data-view-notes]");
  if (viewNotesButton) {
    await loadNotes(viewNotesButton.dataset.viewNotes);
    return;
  }
  const embeddingsButton = event.target.closest("[data-generate-embeddings]");
  if (embeddingsButton) {
    await generateEmbeddings(embeddingsButton.dataset.generateEmbeddings);
    return;
  }
  const searchButton = event.target.closest("[data-search-document]");
  if (searchButton) {
    renderSearchPanel(searchButton.dataset.searchDocument);
  }
});

function formatStepLabel(step) {
  if (!step) return "Processing";
  if (step === "PENDING") return "Pending";
  if (step === "PROCESSING") return "Processing";
  if (step === "RETRYING") return "Retrying";
  return step
    .toLowerCase()
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function renderTaskStatus(tasks) {
  // Filter tasks to show only active ones
  const activeTasks = tasks.filter(t => t.status === "PENDING" || t.status === "PROCESSING" || t.status === "RETRYING");

  if (activeTasks.length === 0) {
    statusCard.innerHTML = `Upload a PDF to create a parsing task.`;
    statusCard.classList.add("muted");
    progressBar.style.width = "0%";
    return;
  }

  statusCard.classList.remove("muted");
  statusCard.innerHTML = activeTasks.map(task => {
    const doc = documentsMap.get(task.documentId);
    const docTitle = doc ? doc.title : (task.documentId ? `Document ${task.documentId.slice(0, 8)}` : "Unknown Document");
    const taskTypeLabel = task.taskType === "PARSE_DOCUMENT" ? "PDF to Markdown" : 
                          task.taskType === "GENERATE_NOTES" ? "AI Notes Generation" :
                          task.taskType === "GENERATE_EMBEDDINGS" ? "Embedding Generation" : task.taskType;
    
    const statusClass = task.status.toLowerCase();
    const errorHtml = task.errorMessage ? `<div class="task-error-msg">${escapeHtml(task.errorMessage)}</div>` : "";
    
    return `
      <div class="task-status-item">
        <div class="task-status-meta">
          <span class="task-doc-title">${escapeHtml(docTitle)}</span>
          <span class="task-type-badge ${task.taskType ? task.taskType.toLowerCase() : ""}">${escapeHtml(taskTypeLabel)}</span>
        </div>
        <div class="task-status-row">
          <div class="task-status-indicator">
            <span class="status-pulse-dot"></span>
            <span class="task-step-label">${escapeHtml(formatStepLabel(task.currentStep || task.status))}</span>
          </div>
          <span class="task-progress-pct">${task.progress}%</span>
        </div>
        ${errorHtml}
        <div class="task-progress-shell">
          <div class="task-progress-bar ${statusClass}" style="width: ${task.progress}%"></div>
        </div>
      </div>
    `;
  }).join("\n");

  const avgProgress = activeTasks.reduce((sum, t) => sum + t.progress, 0) / activeTasks.length;
  progressBar.style.width = `${avgProgress}%`;
}

async function startGlobalPolling() {
  if (globalPollInterval) {
    clearInterval(globalPollInterval);
  }

  const tick = async () => {
    try {
      const [docsResponse, tasksResponse] = await Promise.all([
        fetch(`${API_BASE_URL}/documents`),
        fetch(`${API_BASE_URL}/tasks`)
      ]);

      if (tasksResponse.ok) {
        latestTasksList = await readJson(tasksResponse);
      }

      if (docsResponse.ok) {
        const documents = await readJson(docsResponse);
        documentsMap = new Map(documents.map(d => [d.id, d]));
        renderDocuments(documents);
      }

      if (tasksResponse.ok) {
        renderTaskStatus(latestTasksList);

        // Check if any tracked note generation task has completed
        for (const [taskId, docId] of pendingNotesTasks.entries()) {
          const task = latestTasksList.find(t => t.id === taskId);
          if (task) {
            if (task.status === "COMPLETED") {
              pendingNotesTasks.delete(taskId);
              await loadNotes(docId);
            } else if (task.status === "FAILED" || task.status === "CANCELLED") {
              pendingNotesTasks.delete(taskId);
            }
          }
        }
      }
    } catch (error) {
      console.error("Polling error:", error);
    }
  };

  await tick();
  globalPollInterval = setInterval(tick, 1500);
}

async function loadDocuments() {
  try {
    const response = await fetch(`${API_BASE_URL}/documents`);
    const documents = await readJson(response);
    if (!response.ok) {
      throw new Error(documents.message || "Could not load documents");
    }
    documentsMap = new Map(documents.map(d => [d.id, d]));
    renderDocuments(documents);
  } catch (error) {
    documentsList.innerHTML = `<div class="status-card muted">${escapeHtml(formatFetchError(error))}</div>`;
  }
}

function formatFetchError(error) {
  if (error instanceof TypeError && error.message === "Failed to fetch") {
    return [
      "Could not reach the NoteFlow API.",
      `API: ${API_BASE_URL}`,
      "Check that the API is running and that the frontend origin is allowed by CORS.",
    ].join("\n");
  }
  return error.message || "Unexpected request error";
}

function renderStatus(message, progress) {
  statusCard.textContent = message;
  statusCard.classList.toggle("muted", !message || message.includes("Upload a PDF"));
  progressBar.style.width = `${Math.max(0, Math.min(100, progress))}%`;
}

function renderDocuments(documents) {
  if (!documents.length) {
    documentsList.innerHTML = `<div class="status-card muted">No documents yet.</div>`;
    return;
  }

  documentsList.innerHTML = documents
    .map((document) => {
      // 1. Determine Parse Status Badge
      let parseStatusText = "Parse Unknown";
      let parseStatusClass = "unknown";
      if (document.status === "READY") {
        parseStatusText = "Parse Ready";
        parseStatusClass = "ready";
      } else if (document.status === "PROCESSING" || document.status === "UPLOADED") {
        parseStatusText = "Parse Processing";
        parseStatusClass = "processing";
      } else if (document.status === "FAILED") {
        parseStatusText = "Parse Failed";
        parseStatusClass = "failed";
      }

      // 2. Determine AI Note Status Badge
      let noteStatusText = "AI Note Not Started";
      let noteStatusClass = "muted";
      if (document.aiNoteStatus === "READY") {
        noteStatusText = "AI Note Ready";
        noteStatusClass = "ready";
      } else if (document.aiNoteStatus === "GENERATING" || document.aiNoteStatus === "PROCESSING") {
        noteStatusText = "AI Note Pending";
        noteStatusClass = "processing";
      } else if (document.aiNoteStatus === "FAILED") {
        noteStatusText = "AI Note Failed";
        noteStatusClass = "failed";
      }
      const noteStatusHtml = `<span class="badge ${noteStatusClass}">${escapeHtml(noteStatusText)}</span>`;

      return `
        <article class="document-row">
          <div class="document-main">
            <p class="document-title">${escapeHtml(document.title)}</p>
            <div class="document-meta">
              ${escapeHtml(document.documentType)}
              · ${escapeHtml(document.originalFilename)}
              · ${formatBytes(document.fileSize)}
              ${document.pageCount ? `· ${document.pageCount} pages` : ""}
            </div>
          </div>
          <div class="document-badges">
            <span class="badge ${parseStatusClass}">${escapeHtml(parseStatusText)}</span>
            ${noteStatusHtml}
          </div>
          <div class="row-actions">
            <button class="secondary" type="button" data-view-parse="${escapeHtml(document.id)}">View Parsed Output</button>
            <button class="secondary" type="button" data-view-notes="${escapeHtml(document.id)}">View AI Notes</button>
            <button class="secondary" type="button" data-search-document="${escapeHtml(document.id)}">Search</button>
            <button class="secondary" type="button" data-generate-embeddings="${escapeHtml(document.id)}">Generate Embeddings</button>
            <button type="button" data-generate-notes="${escapeHtml(document.id)}">Generate AI Notes</button>
          </div>
        </article>
      `;
    })
    .join("");
}

async function loadParsedOutput(documentId) {
  parseOutput.classList.remove("muted");
  parseOutput.innerHTML = `<div class="output-title">Parsed Output</div><div class="status-card muted">Loading parsed output...</div>`;
  try {
    const [summaryResponse, chunksResponse, assetsResponse, blocksResponse, regionsResponse, vlmResponse, markdownPagesResponse, markdownResponse] = await Promise.all([
      fetch(`${API_BASE_URL}/documents/${documentId}/parse-result`),
      fetch(`${API_BASE_URL}/documents/${documentId}/chunks`),
      fetch(`${API_BASE_URL}/documents/${documentId}/assets`),
      fetch(`${API_BASE_URL}/documents/${documentId}/layout-blocks`),
      fetch(`${API_BASE_URL}/documents/${documentId}/visual-regions`),
      fetch(`${API_BASE_URL}/documents/${documentId}/vlm-results`),
      fetch(`${API_BASE_URL}/documents/${documentId}/markdown-pages`),
      fetch(`${API_BASE_URL}/documents/${documentId}/markdown`),
    ]);
    const summary = await readJson(summaryResponse);
    const chunks = await readJson(chunksResponse);
    const assets = await readJson(assetsResponse);
    const blocks = await readJson(blocksResponse);
    const regions = await readJson(regionsResponse);
    const vlmResults = await readJson(vlmResponse);
    const markdownPages = await readJson(markdownPagesResponse);
    const markdownDocument = await readJson(markdownResponse);
    if (!summaryResponse.ok) {
      throw new Error(summary.message || "Parse summary is not available yet");
    }
    if (!chunksResponse.ok) {
      throw new Error(chunks.message || "Chunks are not available yet");
    }
    if (!assetsResponse.ok) {
      throw new Error(assets.message || "Visual assets are not available yet");
    }
    if (!blocksResponse.ok) {
      throw new Error(blocks.message || "Layout blocks are not available yet");
    }
    if (!regionsResponse.ok) {
      throw new Error(regions.message || "Visual regions are not available yet");
    }
    if (!vlmResponse.ok) {
      throw new Error(vlmResults.message || "VLM results are not available yet");
    }
    renderParsedOutput(
      summary,
      chunks,
      assets,
      blocks,
      regions,
      vlmResults,
      markdownPagesResponse.ok ? markdownPages : [],
      markdownResponse.ok ? markdownDocument : null
    );
  } catch (error) {
    parseOutput.classList.add("muted");
    parseOutput.textContent = error.message;
  }
}

async function generateNotes(documentId) {
  renderStatus("Creating AI notes task...", 0);
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${documentId}/notes`, {
      method: "POST",
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.message || "Could not create notes task");
    }
    renderStatus(`Created AI notes ${payload.noteId}\nCreated task ${payload.taskId}`, 5);
    pendingNotesTasks.set(payload.taskId, documentId);
  } catch (error) {
    renderStatus(formatFetchError(error), 0);
  }
}

async function generateEmbeddings(documentId) {
  renderStatus("Creating embedding task...", 0);
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${documentId}/embeddings`, {
      method: "POST",
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.message || "Could not create embedding task");
    }
    renderStatus(`Embedding task ${payload.taskId}\nStatus ${payload.status}`, 5);
  } catch (error) {
    renderStatus(formatFetchError(error), 0);
  }
}

async function loadNotes(documentId) {
  parseOutput.classList.remove("muted");
  parseOutput.innerHTML = `<div class="output-title">AI Notes</div><div class="status-card muted">Loading AI notes...</div>`;
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${documentId}/notes`);
    const note = await readJson(response);
    if (!response.ok) {
      throw new Error(note.message || "AI notes are not available yet");
    }
    renderNotes(note);
  } catch (error) {
    parseOutput.classList.add("muted");
    parseOutput.textContent = formatFetchError(error);
  }
}

function renderSearchPanel(documentId = null) {
  const document = documentId ? documentsMap.get(documentId) : null;
  const documents = Array.from(documentsMap.values());
  const scopeLabel = document ? `Search ${document.title}` : "Search all documents";
  parseOutput.classList.remove("muted");
  parseOutput.innerHTML = `
    <div class="output-title">${escapeHtml(scopeLabel)}</div>
    <form id="search-form" class="search-form" data-document-id="${escapeHtml(documentId || "")}">
      <label>
        Query
        <input id="search-query" name="query" type="search" placeholder="Ask about a theorem, formula, example, code snippet..." required />
      </label>
      <div class="search-controls">
        <label>
          Search type
          <select id="search-mode" name="mode">
            <option value="MIXED">Mixed: PDF + AI Note</option>
            <option value="PDF">Original PDF only</option>
            <option value="AI_NOTE">AI Note only</option>
            <option value="CUSTOM">Custom selected files</option>
          </select>
        </label>
        <label>
          Top K
          <input id="search-top-k" name="topK" type="number" min="1" max="30" value="8" />
        </label>
      </div>
      <div id="custom-search-scope" class="custom-search-scope" hidden>
        ${renderCustomSearchScope(documents)}
      </div>
      <div class="search-actions">
        <button type="submit">Search</button>
      </div>
    </form>
    <div id="search-results" class="search-results muted">Generate embeddings before searching a document.</div>
  `;

  const formEl = parseOutput.querySelector("#search-form");
  const modeEl = parseOutput.querySelector("#search-mode");
  const customScopeEl = parseOutput.querySelector("#custom-search-scope");
  modeEl.addEventListener("change", () => {
    customScopeEl.hidden = modeEl.value !== "CUSTOM";
  });
  formEl.addEventListener("submit", executeSearch);
  parseOutput.querySelector("#search-query").focus();
}

function renderCustomSearchScope(documents) {
  if (!documents.length) {
    return `<div class="status-card muted">No documents are available for custom search.</div>`;
  }
  return `
    <div class="source-picker">
      ${documents.map((document) => {
        const ready = document.status === "READY";
        const aiNoteReady = document.aiNoteStatus === "READY";
        return `
          <article class="source-picker-row">
            <div>
              <strong>${escapeHtml(document.title)}</strong>
              <div class="document-meta">${escapeHtml(document.documentType)} · ${escapeHtml(document.originalFilename)}</div>
            </div>
            <label class="checkbox-label">
              <input type="checkbox" name="pdfDocumentIds" value="${escapeHtml(document.id)}" ${ready ? "" : "disabled"} />
              PDF
            </label>
            <label class="checkbox-label">
              <input type="checkbox" name="aiNoteDocumentIds" value="${escapeHtml(document.id)}" ${aiNoteReady ? "" : "disabled"} />
              AI Note
            </label>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

async function executeSearch(event) {
  event.preventDefault();
  const formEl = event.currentTarget;
  const resultsEl = parseOutput.querySelector("#search-results");
  const documentId = formEl.dataset.documentId || null;
  const mode = formEl.querySelector("#search-mode").value;
  const query = formEl.querySelector("#search-query").value.trim();
  const topK = Number(formEl.querySelector("#search-top-k").value || 8);
  const body = { query, topK, mode };

  if (mode === "CUSTOM") {
    body.pdfDocumentIds = checkedValues(formEl, "pdfDocumentIds");
    body.aiNoteDocumentIds = checkedValues(formEl, "aiNoteDocumentIds");
    if (!body.pdfDocumentIds.length && !body.aiNoteDocumentIds.length) {
      resultsEl.classList.add("muted");
      resultsEl.textContent = "Choose at least one PDF or AI Note for custom search.";
      return;
    }
  }

  const endpoint = documentId && mode !== "CUSTOM"
    ? `${API_BASE_URL}/documents/${documentId}/search`
    : `${API_BASE_URL}/search`;

  resultsEl.classList.remove("muted");
  resultsEl.innerHTML = `<div class="status-card muted">Searching...</div>`;
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.message || "Search failed");
    }
    renderSearchResults(payload);
  } catch (error) {
    resultsEl.classList.add("muted");
    resultsEl.textContent = formatFetchError(error);
  }
}

function renderSearchResults(payload) {
  const resultsEl = parseOutput.querySelector("#search-results");
  if (!payload.results || !payload.results.length) {
    resultsEl.classList.add("muted");
    resultsEl.textContent = "No matching embedded source was found. Generate embeddings or broaden the search scope.";
    return;
  }
  resultsEl.classList.remove("muted");
  resultsEl.innerHTML = payload.results.map(renderSearchResult).join("");
}

function renderSearchResult(result) {
  const sourceLabel = result.sourceDomain === "AI_NOTE" ? "AI Note" : "PDF";
  const document = documentsMap.get(result.documentId);
  const pageLabel = result.pageStart && result.pageEnd && result.pageEnd !== result.pageStart
    ? `Pages ${result.pageStart}-${result.pageEnd}`
    : result.pageStart
      ? `Page ${result.pageStart}`
      : "Page unknown";
  return `
    <article class="search-result-card">
      <div class="search-result-header">
        <span class="badge ${result.sourceDomain === "AI_NOTE" ? "note-source" : "pdf-source"}">${escapeHtml(sourceLabel)}</span>
        <span>${escapeHtml(pageLabel)} · score ${Number(result.score || 0).toFixed(3)}</span>
      </div>
      <strong>${escapeHtml(result.title || sourceLabel)}</strong>
      <div class="document-meta">${escapeHtml(document?.title || result.documentId)}</div>
      <p>${escapeHtml(result.snippet || "No preview available.")}</p>
    </article>
  `;
}

function checkedValues(formEl, name) {
  return Array.from(formEl.querySelectorAll(`input[name="${name}"]:checked`)).map((input) => input.value);
}

function renderNotes(note) {
  const report = parseJsonSafe(note.qualityReportJson) || {};
  const coverage = report.coveredPageStart && report.coveredPageEnd
    ? `${report.coveredPageStart}-${report.coveredPageEnd}`
    : "-";
  parseOutput.innerHTML = `
    <div class="output-title">AI Notes</div>
    <div class="summary-grid">
      ${summaryItem("Status", note.status)}
      ${summaryItem("Version", note.noteVersion)}
      ${summaryItem("Provider", note.modelProvider || "-")}
      ${summaryItem("Model", note.modelName || "-")}
      ${summaryItem("Sections", report.sectionCount || "-")}
      ${summaryItem("Coverage", coverage)}
      ${summaryItem("Confidence", report.averageConfidence ?? "-")}
    </div>
    <div class="preview-block notes-preview">
      <strong>${escapeHtml(note.title || "AI Notes")}</strong>
      ${note.summary ? `<p>${escapeHtml(note.summary)}</p>` : ""}
      <pre>${escapeHtml(note.markdown || "Notes are not ready yet.")}</pre>
    </div>
  `;
}

function renderParsedOutput(summary, chunks, assets, blocks, regions, vlmResults, markdownPages, markdownDocument) {
  const visualAssetCount = assets.filter((asset) => asset.visualSummary).length;
  const blockCounts = countBy(blocks, "blockType");
  const successfulVlm = vlmResults.filter((result) => result.searchText || result.description || result.transcription).length;
  const markdownQuality = markdownDocument ? parseJsonSafe(markdownDocument.qualityReportJson) : null;
  parseOutput.innerHTML = `
    <div class="output-title">Parsed Output</div>
    <div class="summary-grid">
      ${summaryItem("Parser", summary.parserName)}
      ${summaryItem("Pages", summary.pageCount)}
      ${summaryItem("Text chars", summary.extractedTextLength)}
      ${summaryItem("Source", summary.detectedContentSourceType)}
      ${summaryItem("Visual pages", visualAssetCount)}
      ${summaryItem("Layout blocks", blocks.length)}
      ${summaryItem("Visual regions", regions.length)}
      ${summaryItem("VLM results", `${successfulVlm}/${vlmResults.length}`)}
      ${summaryItem("Markdown pages", markdownPages.length || "-")}
      ${summaryItem("Markdown quality", markdownQuality?.averageQualityScore ?? "-")}
    </div>
    <div class="layout-blocks">
      ${Object.entries(blockCounts).map(([type, count]) => `<span class="pill">${escapeHtml(type)} ${count}</span>`).join("")}
    </div>
    ${regions.length ? renderVisionPanel(regions, vlmResults) : ""}
    ${markdownDocument ? renderMarkdownPanel(markdownDocument, markdownPages) : ""}
    <div class="preview-block">
      <strong>Preview</strong>
      <pre>${escapeHtml(summary.extractedTextPreview || "No extractable text preview.")}</pre>
    </div>
    <div class="documents-list">
      ${chunks.length ? chunks.map((chunk) => renderChunk(chunk, assets)).join("") : `<div class="status-card muted">No chunks were extracted.</div>`}
    </div>
  `;
}

function renderMarkdownPanel(markdownDocument, markdownPages) {
  const report = parseJsonSafe(markdownDocument.qualityReportJson);
  const warningCounts = report?.warningCounts || {};
  return `
    <div class="preview-block">
      <strong>Markdown document</strong>
      <div class="layout-blocks">
        ${Object.entries(warningCounts).map(([warning, count]) => `<span class="pill">${escapeHtml(warning)} ${count}</span>`).join("")}
      </div>
      <pre>${escapeHtml(markdownDocument.markdown || "No Markdown generated.")}</pre>
    </div>
    <div class="documents-list">
      ${markdownPages.map((page) => renderMarkdownPage(page)).join("")}
    </div>
  `;
}

function renderMarkdownPage(page) {
  const warnings = parseJsonSafe(page.warningsJson) || [];
  return `
    <article class="chunk-card">
      <div class="chunk-header">
        <span>Markdown page ${page.pageNumber} · ${escapeHtml(page.sourceType)}</span>
        <span>quality ${page.qualityScore}</span>
      </div>
      ${warnings.length ? `<div class="layout-blocks">${warnings.map((warning) => `<span class="pill">${escapeHtml(warning)}</span>`).join("")}</div>` : ""}
      <pre>${escapeHtml(page.markdown)}</pre>
    </article>
  `;
}

function renderVisionPanel(regions, vlmResults) {
  const resultByRegion = new Map(vlmResults.map((result) => [`${result.pageNumber}:${result.regionIndex}`, result]));
  return `
    <div class="vision-panel">
      <strong>Visual regions</strong>
      <div class="region-grid">
        ${regions.map((region) => renderRegion(region, resultByRegion.get(`${region.pageNumber}:${region.regionIndex}`))).join("")}
      </div>
    </div>
  `;
}

function renderRegion(region, result) {
  const title = `Page ${region.pageNumber} · ${region.regionType} · region ${region.regionIndex}`;
  const description = result?.description || result?.transcription || result?.searchText || result?.errorMessage || "No VLM result yet.";
  return `
    <article class="region-card">
      <a href="${escapeHtml(API_BASE_URL + region.url)}" target="_blank" rel="noreferrer">
        <img src="${escapeHtml(API_BASE_URL + region.url)}" alt="${escapeHtml(title)}">
      </a>
      <div class="region-body">
        <div class="region-title">${escapeHtml(title)}</div>
        <div class="region-provider">${escapeHtml(result ? `${result.provider} · ${result.model}` : "pending")}</div>
        <p>${escapeHtml(description)}</p>
      </div>
    </article>
  `;
}

function summaryItem(label, value) {
  return `
    <div class="summary-item">
      <div class="summary-label">${escapeHtml(label)}</div>
      <div class="summary-value">${escapeHtml(value ?? "-")}</div>
    </div>
  `;
}

function renderChunk(chunk, assets) {
  const pageLabel = chunk.pageEnd && chunk.pageEnd !== chunk.pageStart
    ? `Pages ${chunk.pageStart}-${chunk.pageEnd}`
    : `Page ${chunk.pageStart || chunk.pageNumber}`;
  const section = chunk.sectionTitle ? ` · ${chunk.sectionTitle}` : "";
  const type = chunk.chunkType || "PARAGRAPH";
  const chunkAssets = chunk.sourceAssetId
    ? assets.filter((asset) => asset.id === chunk.sourceAssetId)
    : [];
  return `
    <article class="chunk-card">
      <div class="chunk-header">
        <span>Chunk ${chunk.chunkIndex} · ${escapeHtml(type)}${escapeHtml(section)}</span>
        <span>${escapeHtml(pageLabel)} · ${chunk.tokenCount ?? 0} tokens</span>
      </div>
      ${chunkAssets.length ? renderAssets(chunkAssets) : ""}
      <pre>${escapeHtml(chunk.content)}</pre>
    </article>
  `;
}

function renderAssets(assets) {
  return `
    <div class="asset-strip">
      ${assets.map((asset) => `
        <figure class="page-asset">
          <a href="${escapeHtml(API_BASE_URL + asset.url)}" target="_blank" rel="noreferrer">
            <img src="${escapeHtml(API_BASE_URL + asset.url)}" alt="Page ${asset.pageNumber} render">
          </a>
          <figcaption>
            Page ${asset.pageNumber}
            · images ${asset.imageCount}
            · drawings ${asset.drawingCount}
            · ${(asset.imageCoverage * 100).toFixed(1)}%
          </figcaption>
        </figure>
      `).join("")}
    </div>
  `;
}

function countBy(items, key) {
  return items.reduce((counts, item) => {
    const value = item[key] || "UNKNOWN";
    counts[value] = (counts[value] || 0) + 1;
    return counts;
  }, {});
}

async function readJson(response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function parseJsonSafe(value) {
  if (!value) {
    return null;
  }
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function formatBytes(bytes) {
  if (!bytes) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

loadDocuments().then(() => startGlobalPolling());
