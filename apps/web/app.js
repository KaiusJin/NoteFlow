const API_BASE_URL = localStorage.getItem("noteflowApiBaseUrl") || "http://localhost:8080";

const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#pdf-file");
const fileLabel = document.querySelector("#file-label");
const statusCard = document.querySelector("#status-card");
const progressBar = document.querySelector("#progress-bar");
const documentsList = document.querySelector("#documents-list");
const refreshDocuments = document.querySelector("#refresh-documents");
const parseOutput = document.querySelector("#parse-output");

let activePoll = null;

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
    pollTask(payload.taskId);
  } catch (error) {
    renderStatus(error.message, 0);
  } finally {
    submitButton.disabled = false;
  }
});

refreshDocuments.addEventListener("click", loadDocuments);

documentsList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-view-parse]");
  if (!button) {
    return;
  }
  await loadParsedOutput(button.dataset.viewParse);
});

async function pollTask(taskId) {
  if (activePoll) {
    clearInterval(activePoll);
  }

  const tick = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/tasks/${taskId}`);
      const task = await readJson(response);
      if (!response.ok) {
        throw new Error(task.message || "Could not load task");
      }

      renderStatus(
        [
          `Task: ${task.id}`,
          `Status: ${task.status}`,
          `Step: ${task.currentStep}`,
          `Progress: ${task.progress}%`,
          task.errorMessage ? `Error: ${task.errorMessage}` : "",
        ]
          .filter(Boolean)
          .join("\n"),
        task.progress
      );

      if (task.status === "COMPLETED" || task.status === "FAILED" || task.status === "CANCELLED") {
        clearInterval(activePoll);
        activePoll = null;
        await loadDocuments();
      }
    } catch (error) {
      renderStatus(error.message, 0);
      clearInterval(activePoll);
      activePoll = null;
    }
  };

  await tick();
  activePoll = setInterval(tick, 1500);
}

async function loadDocuments() {
  try {
    const response = await fetch(`${API_BASE_URL}/documents`);
    const documents = await readJson(response);
    if (!response.ok) {
      throw new Error(documents.message || "Could not load documents");
    }
    renderDocuments(documents);
  } catch (error) {
    documentsList.innerHTML = `<div class="status-card muted">${escapeHtml(error.message)}</div>`;
  }
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
      const statusClass = document.status.toLowerCase();
      return `
        <article class="document-row">
          <div>
            <p class="document-title">${escapeHtml(document.title)}</p>
            <div class="document-meta">
              ${escapeHtml(document.documentType)}
              · ${escapeHtml(document.originalFilename)}
              · ${formatBytes(document.fileSize)}
              ${document.pageCount ? `· ${document.pageCount} pages` : ""}
            </div>
          </div>
          <span class="badge ${statusClass}">${escapeHtml(document.status)}</span>
          <button class="secondary" type="button" data-view-parse="${escapeHtml(document.id)}">
            View parsed output
          </button>
        </article>
      `;
    })
    .join("");
}

async function loadParsedOutput(documentId) {
  parseOutput.classList.remove("muted");
  parseOutput.textContent = "Loading parsed output...";
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

function renderParsedOutput(summary, chunks, assets, blocks, regions, vlmResults, markdownPages, markdownDocument) {
  const visualAssetCount = assets.filter((asset) => asset.visualSummary).length;
  const blockCounts = countBy(blocks, "blockType");
  const successfulVlm = vlmResults.filter((result) => result.searchText || result.description || result.transcription).length;
  const markdownQuality = markdownDocument ? parseJsonSafe(markdownDocument.qualityReportJson) : null;
  parseOutput.innerHTML = `
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

loadDocuments();
