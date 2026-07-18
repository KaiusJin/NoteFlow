const API_BASE_URL = localStorage.getItem("noteflowApiBaseUrl") || "http://localhost:8080";

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------
let documentsMap = new Map();
let latestTasksList = [];
let previousTaskStatuses = new Map();
let activeDocumentId = localStorage.getItem("noteflowActiveDocument") || null;
let currentView = localStorage.getItem("noteflowView") || "agent";
const pendingNotesTasks = new Map(); // taskId -> documentId
const chatMessages = [];
let activeConversationId = localStorage.getItem("noteflowConversationId") || null;
let conversationHydrated = false;
let attemptPollTimer = null;
let globalPollInterval = null;

const viewRoot = document.querySelector("#view-root");
const sidebarTasks = document.querySelector("#sidebar-tasks");

// Per-module selection UIs replaced the old shared sidebar document list:
// - AI Agent: "+ Sources" modal (multi-select across Markdown / AI Notes)
// - Flashcards & Quiz: right-side file panel with per-file generate buttons
// - Editor: top document tabs
let agentSources = parseJsonSafe(localStorage.getItem("noteflowAgentSources")) || { pdf: [], aiNote: [] };
let editorTabs = parseJsonSafe(localStorage.getItem("noteflowEditorTabs")) || [];
let editorSidebarFolderId = localStorage.getItem("noteflowEditorSidebarFolder") || null;

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
const VIEWS = {
  agent: renderAgentView,
  editor: renderEditorView,
  folders: renderFoldersView,
  flashcards: renderFlashcardsView,
  quiz: renderQuizView,
  general: renderGeneralView,
};

function navigate(view) {
  if (!VIEWS[view]) view = "agent";
  currentView = view;
  localStorage.setItem("noteflowView", view);
  stopAttemptPolling();
  teardownEditor();
  document.querySelectorAll("[data-nav]").forEach((button) => {
    button.classList.toggle("active", button.dataset.nav === view);
  });
  viewRoot.classList.toggle("editor-mode", view === "editor");
  viewRoot.classList.toggle("agent-mode", view === "agent");
  VIEWS[view]();
}

function activeDocument() {
  return activeDocumentId ? documentsMap.get(activeDocumentId) : null;
}

function selectDocument(documentId) {
  activeDocumentId = documentId;
  if (currentView === "editor") {
    editorSidebarFolderId = null;
    localStorage.removeItem("noteflowEditorSidebarFolder");
  }
  localStorage.setItem("noteflowActiveDocument", documentId || "");
  if (currentView === "flashcards" || currentView === "quiz" || currentView === "editor") {
    navigate(currentView);
  }
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function renderSidebarTasks() {
  const active = latestTasksList.filter((t) => ["PENDING", "PROCESSING", "RETRYING"].includes(t.status));
  if (!active.length) {
    sidebarTasks.innerHTML = "";
    return;
  }
  sidebarTasks.innerHTML = active
    .map((task) => `
      <div class="side-task">
        <span class="status-pulse-dot"></span>
        <span class="side-task-label">${escapeHtml(taskTypeLabel(task.taskType))}</span>
        <span class="side-task-pct">${task.progress}%</span>
      </div>
    `)
    .join("");
}

function taskTypeLabel(taskType) {
  return {
    PARSE_DOCUMENT: "PDF to Markdown",
    GENERATE_NOTES: "AI Notes",
    GENERATE_FLASHCARDS: "Flashcards",
    GENERATE_QUIZ: "Quiz",
    GRADE_QUIZ_ATTEMPT: "Quiz Grading",
    GENERATE_EMBEDDINGS: "Embeddings",
  }[taskType] || taskType;
}

function formatStepLabel(step) {
  if (!step) return "Processing";
  if (["PENDING", "PROCESSING", "RETRYING"].includes(step)) {
    return step.charAt(0) + step.slice(1).toLowerCase();
  }
  return step.toLowerCase().split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------
let pollFailureCount = 0;

async function startGlobalPolling() {
  if (globalPollInterval) clearInterval(globalPollInterval);
  const tick = async () => {
    // Back off while the API is unreachable: poll every 10s instead of 1.5s
    // and log the outage once instead of flooding the console.
    if (pollFailureCount >= 3 && pollFailureCount % 7 !== 0) {
      pollFailureCount += 1;
      return;
    }
    try {
      const [docsResponse, tasksResponse] = await Promise.all([
        fetch(`${API_BASE_URL}/documents`),
        fetch(`${API_BASE_URL}/tasks`),
      ]);
      pollFailureCount = 0;
      if (docsResponse.ok) {
        const documents = await readJson(docsResponse);
        documentsMap = new Map(documents.map((d) => [d.id, d]));
        if (activeDocumentId && !documentsMap.has(activeDocumentId)) activeDocumentId = null;
        const generalDocs = viewRoot.querySelector("#general-documents");
        if (generalDocs) renderGeneralDocuments(generalDocs, documents);
      }
      if (tasksResponse.ok) {
        latestTasksList = await readJson(tasksResponse);
        renderSidebarTasks();
        const generalStatus = viewRoot.querySelector("#general-task-status");
        if (generalStatus) renderGeneralTaskStatus(generalStatus);
        handleTaskTransitions();
      }
    } catch (error) {
      pollFailureCount += 1;
      if (pollFailureCount <= 3) console.error("Polling error:", error);
    }
  };
  await tick();
  globalPollInterval = setInterval(tick, 1500);
}

function handleTaskTransitions() {
  for (const task of latestTasksList) {
    const previous = previousTaskStatuses.get(task.id);
    previousTaskStatuses.set(task.id, task.status);
    if (previous === task.status || task.status !== "COMPLETED") continue;

    if (pendingNotesTasks.has(task.id)) {
      const documentId = pendingNotesTasks.get(task.id);
      pendingNotesTasks.delete(task.id);
      if (currentView === "general") loadNotes(documentId);
    }
    if (task.taskType === "GENERATE_FLASHCARDS" && currentView === "flashcards") {
      navigate("flashcards");
    }
    if (task.taskType === "GENERATE_QUIZ" && currentView === "quiz" && !viewRoot.querySelector("#quiz-attempt-form")) {
      navigate("quiz");
    }
  }
}

// ---------------------------------------------------------------------------
// View: AI Agent (persistent retrieval-grounded conversation)
// ---------------------------------------------------------------------------
function agentSourcesSummary() {
  const count = agentSources.pdf.length + agentSources.aiNote.length;
  return count ? `Scope: ${count} source${count === 1 ? "" : "s"} selected` : "Scope: all sources";
}

function renderAgentView() {
  viewRoot.innerHTML = `
    <div class="view-header">
      <div>
        <div class="eyebrow">AI Agent</div>
        <h1>Ask your study material</h1>
      </div>
    </div>
    <div class="chat-shell">
      <div id="chat-messages" class="chat-messages">
        ${chatMessages.length ? chatMessages.map(renderChatMessage).join("") : `
          <div class="chat-empty">
            <div class="chat-empty-mark">✦</div>
          </div>`}
      </div>
      <form id="chat-form" class="chat-composer">
        <div class="chat-input-row">
          <input id="chat-query" type="text" placeholder="Ask anything about your documents…" autocomplete="off" required />
          <button type="submit">Send</button>
        </div>
        <div class="chat-source-row">
          <button type="button" id="chat-open-sources" class="chip-button" title="Choose which files ground the answers">＋ Sources</button>
          <span id="chat-source-summary" class="chat-scope-hint" title="Current retrieval scope for your questions">${escapeHtml(agentSourcesSummary())}</span>
        </div>
      </form>
    </div>
    ${renderSourceModal()}
  `;
  wireSourceModal();
  scrollChatToBottom();
  viewRoot.querySelector("#chat-query").focus();
  if (activeConversationId && !conversationHydrated && !chatMessages.length) hydrateConversation();
}

// Centered modal for picking retrieval sources, grouped Markdown / AI Notes.
// Selections can mix both groups; an empty selection means "all sources".
function renderSourceModal() {
  const documents = Array.from(documentsMap.values());
  const row = (doc, kind, ready, checked) => `
    <label class="source-row ${ready ? "" : "disabled"}">
      <input type="checkbox" data-source-kind="${kind}" value="${escapeHtml(doc.id)}" ${checked ? "checked" : ""} ${ready ? "" : "disabled"} />
      <span class="source-row-title">${escapeHtml(doc.title)}</span>
      <span class="source-row-meta">${doc.pageCount ? `${doc.pageCount} pages` : escapeHtml(doc.documentType || "")}</span>
    </label>
  `;
  return `
    <div id="source-modal" class="modal-overlay" hidden>
      <div class="modal-card">
        <div class="modal-head">
          <h3>Sources</h3>
          <button type="button" class="icon-button" id="source-modal-close" title="Close">✕</button>
        </div>
        <p class="modal-sub">Pick the files that ground the answers. Leave everything unchecked to search all sources.</p>
        <div class="modal-body">
          <div class="source-group-label">Markdown</div>
          ${documents.map((doc) => row(doc, "pdf", doc.status === "READY", agentSources.pdf.includes(doc.id))).join("") || `<div class="side-empty">No documents yet.</div>`}
          <div class="source-group-label">AI Notes</div>
          ${documents.map((doc) => row(doc, "aiNote", doc.aiNoteStatus === "READY", agentSources.aiNote.includes(doc.id))).join("") || `<div class="side-empty">No documents yet.</div>`}
        </div>
        <div class="modal-foot">
          <button type="button" class="ghost-button" id="source-clear">Clear all</button>
          <button type="button" id="source-done">Done</button>
        </div>
      </div>
    </div>
  `;
}

function wireSourceModal() {
  const modal = viewRoot.querySelector("#source-modal");
  if (!modal) return;
  const applyAndClose = () => {
    agentSources = {
      pdf: Array.from(modal.querySelectorAll('input[data-source-kind="pdf"]:checked')).map((input) => input.value),
      aiNote: Array.from(modal.querySelectorAll('input[data-source-kind="aiNote"]:checked')).map((input) => input.value),
    };
    localStorage.setItem("noteflowAgentSources", JSON.stringify(agentSources));
    const summary = viewRoot.querySelector("#chat-source-summary");
    if (summary) summary.textContent = agentSourcesSummary();
    modal.hidden = true;
  };
  viewRoot.querySelector("#chat-open-sources").addEventListener("click", () => {
    modal.hidden = false;
  });
  viewRoot.querySelector("#source-modal-close").addEventListener("click", applyAndClose);
  viewRoot.querySelector("#source-done").addEventListener("click", applyAndClose);
  viewRoot.querySelector("#source-clear").addEventListener("click", () => {
    modal.querySelectorAll("input[type=checkbox]").forEach((input) => { input.checked = false; });
  });
  modal.addEventListener("click", (event) => {
    if (event.target === modal) applyAndClose();
  });
}

function renderChatMessage(message) {
  if (message.role === "user") {
    return `<div class="chat-message user"><div class="bubble">${escapeHtml(message.text)}</div></div>`;
  }
  return `<div class="chat-message assistant"><div class="bubble">${message.html}</div></div>`;
}

function scrollChatToBottom() {
  const messages = viewRoot.querySelector("#chat-messages");
  if (messages) messages.scrollTop = messages.scrollHeight;
}

async function handleChatSubmit(form) {
  const queryInput = form.querySelector("#chat-query");
  const query = queryInput.value.trim();
  if (!query) return;
  // Empty selection means "all sources" (backend treats empty id lists as unscoped).
  const sourceScope = {
    pdfDocumentIds: agentSources.pdf.filter((id) => documentsMap.has(id)),
    aiNoteDocumentIds: agentSources.aiNote.filter((id) => documentsMap.has(id)),
  };

  chatMessages.push({ role: "user", text: query });
  const pendingIndex = chatMessages.push({ role: "assistant", html: `<p class="chat-note">Reading context and sources…</p>` }) - 1;
  refreshChatMessages();
  queryInput.value = "";

  try {
    if (!activeConversationId) {
      const created = await requestJson("/conversations", "POST", { title: query.slice(0, 80) });
      activeConversationId = created.id;
      localStorage.setItem("noteflowConversationId", activeConversationId);
    }
    const response = await fetch(`${API_BASE_URL}/conversations/${activeConversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: query, ...sourceScope }),
    });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.message || "Unable to send message");
    const answer = await pollConversationMessage(payload.assistantMessageId);
    chatMessages[pendingIndex] = { role: "assistant", html: renderConversationAnswer(answer) };
  } catch (error) {
    chatMessages[pendingIndex] = {
      role: "assistant",
      html: `<p class="chat-note">${escapeHtml(formatFetchError(error))}</p>`,
    };
  }
  refreshChatMessages();
}

async function pollConversationMessage(messageId) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const message = await requestJson(`/conversations/messages/${messageId}`);
    if (message.status === "COMPLETED") return message;
    if (message.status === "FAILED") throw new Error(message.error_message || "Answer generation failed");
    await new Promise((resolve) => setTimeout(resolve, attempt < 20 ? 1000 : 3000));
  }
  throw new Error("Answer generation timed out. The conversation remains saved; try again shortly.");
}

function renderConversationAnswer(message) {
  const content = renderRich(message.content_markdown || "No answer was generated.");
  const citations = message.citations || [];
  return `
    <div class="chat-answer-text rich">${content}</div>
    ${citations.length ? `<div class="chat-evidence">${citations.map(renderConversationCitation).join("")}</div>` : ""}
  `;
}

function renderConversationCitation(citation) {
  const start = citation.page_start;
  const end = citation.page_end;
  const page = start ? (end && end !== start ? `Pages ${start}-${end}` : `Page ${start}`) : "Page unknown";
  return `
    <article class="search-result-card">
      <div class="search-result-header"><span class="badge pdf-source">[${Number(citation.citation_index) + 1}]</span><span>${escapeHtml(page)}</span></div>
      <strong>${escapeHtml(citation.document_title || "Source")}</strong>
      <p class="rich">${renderRich(citation.quote_text || "Cited source passage")}</p>
    </article>
  `;
}

async function hydrateConversation() {
  conversationHydrated = true;
  try {
    const stored = await requestJson(`/conversations/${activeConversationId}/messages`);
    for (const message of stored) {
      if (message.role === "USER") {
        chatMessages.push({ role: "user", text: message.content_markdown || "" });
      } else if (message.status === "COMPLETED") {
        chatMessages.push({ role: "assistant", html: renderConversationAnswer(message) });
      } else if (message.status === "FAILED") {
        chatMessages.push({ role: "assistant", html: `<p class="chat-note">${escapeHtml(message.error_message || "Answer generation failed")}</p>` });
      } else {
        chatMessages.push({ role: "assistant", html: `<p class="chat-note">Answer generation is still in progress…</p>` });
      }
    }
    refreshChatMessages();
  } catch {
    activeConversationId = null;
    localStorage.removeItem("noteflowConversationId");
  }
}

function refreshChatMessages() {
  const messages = viewRoot.querySelector("#chat-messages");
  if (!messages) return;
  messages.innerHTML = chatMessages.map(renderChatMessage).join("");
  scrollChatToBottom();
}

// ---------------------------------------------------------------------------
// View: Flashcards
// ---------------------------------------------------------------------------

// Right-side file panel shared by Flashcards and Quiz: every document listed
// under Markdown and (when ready) AI Notes, each with a per-file generate
// button. Both groups hit the same per-document generation endpoint; the
// grouping is presentational.
function renderStudySourcePanel(kind) {
  const documents = Array.from(documentsMap.values());
  const generateAction = kind === "quiz" ? "generate-quiz" : "generate-cards";
  const generateTitle = kind === "quiz" ? "Generate a quiz from this file" : "Generate flashcards from this file";
  const card = (doc, ready) => `
    <article class="study-source-card ${doc.id === activeDocumentId ? "active" : ""}" data-doc-select="${escapeHtml(doc.id)}">
      <div class="source-card-main">
        <strong>${escapeHtml(doc.title)}</strong>
        <span class="source-card-meta">${doc.pageCount ? `${doc.pageCount} pages` : escapeHtml(doc.documentType || "")}</span>
      </div>
      <button type="button" class="source-generate" data-study-action="${generateAction}" data-doc-id="${escapeHtml(doc.id)}" ${ready ? "" : "disabled"} title="${generateTitle}">＋</button>
    </article>
  `;
  const aiNoteDocs = documents.filter((doc) => doc.aiNoteStatus === "READY");
  return `
    <aside class="study-sources">
      <div class="study-sources-title">Files</div>
      <div class="source-group-label">Markdown</div>
      ${documents.map((doc) => card(doc, doc.status === "READY")).join("") || `<div class="side-empty">No documents yet. Upload one in General.</div>`}
      <div class="source-group-label">AI Notes</div>
      ${aiNoteDocs.map((doc) => card(doc, true)).join("") || `<div class="side-empty">No AI notes ready.</div>`}
    </aside>
  `;
}

function studyEmptyMain(icon, hint) {
  return `
    <div class="empty-view study-empty-main">
      <div class="chat-empty-mark">${icon}</div>
      <p>${escapeHtml(hint)}</p>
    </div>
  `;
}

async function renderFlashcardsView() {
  const doc = activeDocument();
  let mainHtml;
  if (!doc) {
    mainHtml = studyEmptyMain("▣", "Pick a file on the right, or press ＋ to generate a deck from it.");
  } else {
    try {
      const response = await fetch(`${API_BASE_URL}/documents/${doc.id}/flashcard-decks`);
      const decks = await readJson(response);
      if (!response.ok) throw new Error(decks.message || "Could not load flashcard decks");
      const deck = decks[0];
      mainHtml = `
        <article class="study-module flashcard-module">
          <div class="study-module-head">
            <div><span class="study-icon">▣</span><h3>${escapeHtml(doc.title)}</h3></div>
            <span class="badge ${statusClass(deck?.status)}">${escapeHtml(deck?.status || "NOT STARTED")}</span>
          </div>
          <p>Source-grounded cards with SM-2 scheduling and page citations.</p>
          ${deck ? studyProgress(deck) : `<div class="empty-study">No deck generated yet.</div>`}
          ${decks.length > 1 ? `<div class="study-history">History: ${decks.map((d) => `v${d.version} ${escapeHtml(d.status)}`).join(" · ")}</div>` : ""}
          <div class="study-actions">
            <button data-study-action="generate-cards" ${doc.status === "READY" ? "" : "disabled"}>${deck ? "Generate new deck" : "Generate flashcards"}</button>
            ${deck?.status === "READY" ? `
              <button class="secondary" data-study-action="review" data-deck-id="${deck.id}">Review due cards</button>
              <button class="secondary" data-study-action="browse-cards" data-deck-id="${deck.id}">Browse all cards</button>
            ` : ""}
          </div>
        </article>
      `;
    } catch (error) {
      mainHtml = `<div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>`;
    }
  }
  viewRoot.innerHTML = `
    <div class="view-header">
      <div>
        <div class="eyebrow">Flashcards${doc ? ` · ${escapeHtml(doc.title)}` : ""}</div>
        <h1>Spaced repetition</h1>
      </div>
      <button class="secondary" type="button" data-refresh-view="flashcards">Refresh</button>
    </div>
    <div class="study-layout">
      <div class="study-main">
        ${mainHtml}
        <div id="study-detail" class="study-detail"></div>
      </div>
      ${renderStudySourcePanel("cards")}
    </div>
  `;
}

async function renderReview(deckId) {
  const detail = viewRoot.querySelector("#study-detail");
  detail.innerHTML = `<div class="study-loading">Loading due cards…</div>`;
  const response = await fetch(`${API_BASE_URL}/flashcard-decks/${deckId}/reviews/due`);
  const cards = await readJson(response);
  if (!response.ok) throw new Error(cards.message || "Could not load review cards");
  detail.innerHTML = `
    <div class="study-toolbar">
      <div><div class="eyebrow">Spaced repetition</div><h3>${cards.length} card${cards.length === 1 ? "" : "s"} due</h3></div>
    </div>
    <div class="flashcard-stack">
      ${cards.length ? cards.map((card) => `
        <article class="review-card">
          <div class="card-meta">
            <span class="pill">${escapeHtml(card.card_type)}</span>
            <span>${escapeHtml(card.topic)} · pages ${escapeHtml(parseJsonSafe(card.source_pages_json)?.join(", ") || "-")}</span>
          </div>
          <h3 class="rich">${renderRich(card.front)}</h3>
          ${card.card_type === "CLOZE" && card.cloze_text ? `<p class="cloze-line rich">${renderRich(card.cloze_text)}</p>` : ""}
          <details>
            <summary>Reveal answer</summary>
            <div class="card-answer rich">${renderRich(card.back)}</div>
            ${card.hint ? `<div class="card-hint">Hint: ${escapeHtml(card.hint)}</div>` : ""}
            <div class="review-grades">
              ${["AGAIN", "HARD", "GOOD", "EASY"].map((grade) => `
                <button class="grade-${grade.toLowerCase()}" data-study-action="grade-card" data-grade="${grade}" data-card-id="${card.id}" data-deck-id="${deckId}">${grade}</button>
              `).join("")}
            </div>
          </details>
        </article>
      `).join("") : `<div class="empty-study">Nothing due. Nice work.</div>`}
    </div>
  `;
}

async function renderCardBrowser(deckId) {
  const detail = viewRoot.querySelector("#study-detail");
  detail.innerHTML = `<div class="study-loading">Loading cards…</div>`;
  const response = await fetch(`${API_BASE_URL}/flashcard-decks/${deckId}/cards`);
  const cards = await readJson(response);
  if (!response.ok) throw new Error(cards.message || "Could not load cards");
  detail.innerHTML = `
    <div class="study-toolbar">
      <div><div class="eyebrow">Deck contents</div><h3>${cards.length} cards</h3></div>
    </div>
    <div class="flashcard-stack">
      ${cards.map((card) => `
        <article class="review-card">
          <div class="card-meta">
            <span class="pill">${escapeHtml(card.card_type)}</span>
            <span class="pill">${escapeHtml(card.difficulty)}</span>
            <span>${escapeHtml(card.topic)} · pages ${escapeHtml(parseJsonSafe(card.source_pages_json)?.join(", ") || "-")}</span>
          </div>
          <h3 class="rich">${renderRich(card.front)}</h3>
          <details><summary>Answer</summary><div class="card-answer rich">${renderRich(card.back)}</div></details>
        </article>
      `).join("")}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// View: Quiz
// ---------------------------------------------------------------------------
async function renderQuizView() {
  const doc = activeDocument();
  let mainHtml;
  if (!doc) {
    mainHtml = studyEmptyMain("?", "Pick a file on the right, or press ＋ to generate a quiz from it.");
  } else {
    try {
      const response = await fetch(`${API_BASE_URL}/documents/${doc.id}/quiz-sets`);
      const quizzes = await readJson(response);
      if (!response.ok) throw new Error(quizzes.message || "Could not load quizzes");
      const quiz = quizzes[0];
      mainHtml = `
        <article class="study-module quiz-module">
          <div class="study-module-head">
            <div><span class="study-icon">?</span><h3>${escapeHtml(doc.title)}</h3></div>
            <span class="badge ${statusClass(quiz?.status)}">${escapeHtml(quiz?.status || "NOT STARTED")}</span>
          </div>
          <p>Mixed-difficulty questions with rubric grading, explanations, and citations.</p>
          ${quiz ? studyProgress(quiz) : `<div class="empty-study">No quiz generated yet.</div>`}
          ${quizzes.length > 1 ? `<div class="study-history">History: ${quizzes.map((q) => `v${q.version} ${escapeHtml(q.status)}`).join(" · ")}</div>` : ""}
          <fieldset class="quiz-options" ${doc.status === "READY" ? "" : "disabled"}>
            <legend>New quiz composition</legend>
            <label>Easy <input type="number" id="quiz-easy" min="0" max="60" value="3" /></label>
            <label>Medium <input type="number" id="quiz-medium" min="0" max="60" value="5" /></label>
            <label>Hard <input type="number" id="quiz-hard" min="0" max="60" value="2" /></label>
            <span class="quiz-total" id="quiz-total">Total: 10</span>
          </fieldset>
          <div class="study-actions">
            <button data-study-action="generate-quiz" ${doc.status === "READY" ? "" : "disabled"}>${quiz ? "Generate new quiz" : "Generate quiz"}</button>
            ${quiz?.status === "READY" ? `<button class="secondary" data-study-action="start-quiz" data-quiz-id="${quiz.id}">Start quiz</button>` : ""}
          </div>
        </article>
      `;
    } catch (error) {
      mainHtml = `<div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>`;
    }
  }
  viewRoot.innerHTML = `
    <div class="view-header">
      <div>
        <div class="eyebrow">Quiz${doc ? ` · ${escapeHtml(doc.title)}` : ""}</div>
        <h1>Practice and grading</h1>
      </div>
      <button class="secondary" type="button" data-refresh-view="quiz">Refresh</button>
    </div>
    <div class="study-layout">
      <div class="study-main">
        ${mainHtml}
        <div id="study-detail" class="study-detail"></div>
      </div>
      ${renderStudySourcePanel("quiz")}
    </div>
  `;
}

async function startQuiz(quizId) {
  const detail = viewRoot.querySelector("#study-detail");
  detail.innerHTML = `<div class="study-loading">Starting attempt…</div>`;
  const [attempt, questionsResponse] = await Promise.all([
    studyPost(`/quiz-sets/${quizId}/attempts`),
    fetch(`${API_BASE_URL}/quiz-sets/${quizId}/questions`),
  ]);
  const questions = await readJson(questionsResponse);
  if (!questionsResponse.ok) throw new Error(questions.message || "Could not load questions");
  detail.innerHTML = `
    <div class="study-toolbar">
      <div><div class="eyebrow">Quiz attempt</div><h3>${questions.length} questions</h3></div>
    </div>
    <form id="quiz-attempt-form" data-attempt-id="${attempt.attemptId}" data-question-ids='${escapeHtml(JSON.stringify(questions.map((q) => q.id)))}' class="quiz-form">
      ${questions.map((q, i) => renderQuizQuestion(q, i)).join("")}
      <button type="submit">Submit quiz</button>
    </form>
  `;
  detail.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderQuizQuestion(question, index) {
  let options = parseJsonSafe(question.options_json) || [];
  if (!options.length && question.question_type === "TRUE_FALSE") {
    options = ["True", "False"];
  }
  const input = options.length
    ? `<div class="quiz-options">${options.map((option) => `
        <label><input type="radio" name="q-${question.id}" value="${escapeHtml(option)}"> <span class="rich">${renderRich(option)}</span></label>
      `).join("")}</div>`
    : `<textarea name="q-${question.id}" rows="5" placeholder="Write your answer…"></textarea>`;
  return `
    <article class="quiz-question">
      <div class="card-meta">
        <span>Question ${index + 1}</span>
        <span class="pill">${escapeHtml(question.question_type)}</span>
        <span>${escapeHtml(question.difficulty)} · ${question.points} pts · pages ${escapeHtml(parseJsonSafe(question.source_pages_json)?.join(", ") || "-")}</span>
      </div>
      <h3 class="rich">${renderRich(question.stem)}</h3>
      ${input}
    </article>
  `;
}

async function submitQuizAttempt(form) {
  const attemptId = form.dataset.attemptId;
  const questionIds = JSON.parse(form.dataset.questionIds);
  for (const questionId of questionIds) {
    const selected = form.querySelector(`[name="q-${questionId}"]:checked`);
    const text = form.querySelector(`textarea[name="q-${questionId}"]`);
    await studyPut(`/quiz-attempts/${attemptId}/answers/${questionId}`, {
      response: selected?.value ?? text?.value ?? "",
    });
  }
  const result = await studyPost(`/quiz-attempts/${attemptId}/submit`);
  await renderAttempt(attemptId, result.status === "GRADING");
}

async function renderAttempt(attemptId, grading = false) {
  const detail = viewRoot.querySelector("#study-detail");
  if (!detail) return;
  const response = await fetch(`${API_BASE_URL}/quiz-attempts/${attemptId}`);
  const result = await readJson(response);
  if (!response.ok) throw new Error(result.message || "Could not load attempt");
  const meta = result.attempt;
  const stillGrading = grading || meta.status === "GRADING";
  detail.innerHTML = `
    <div class="study-toolbar">
      <div><div class="eyebrow">Quiz result</div><h3>${escapeHtml(meta.status)}</h3></div>
      <button class="secondary" data-study-action="view-result" data-attempt-id="${attemptId}">Refresh result</button>
    </div>
    <div class="quiz-score"><strong>${Number(meta.score || 0).toFixed(1)}</strong><span>/ ${Number(meta.max_score || 0).toFixed(1)} points</span></div>
    ${stillGrading ? `<div class="status-card muted">Free-text answers are being graded. This refreshes automatically.</div>` : renderWeakTopics(meta)}
    <div class="answer-review">
      ${(result.answers || []).map((answer, index) => `
        <article class="quiz-question ${answer.is_correct === true ? "answer-correct" : answer.is_correct === false ? "answer-wrong" : ""}">
          <div class="card-meta">Question ${index + 1} · ${escapeHtml(answer.question_type)} · ${answer.awarded_points ?? "-"}/${answer.points}</div>
          <h3 class="rich">${renderRich(answer.stem)}</h3>
          <p><strong>Your answer:</strong> ${escapeHtml(answer.user_response || "No answer")}</p>
          ${answer.feedback ? `<p><strong>Feedback:</strong> ${escapeHtml(answer.feedback)}</p>` : ""}
          ${answer.explanation ? `<details><summary>Explanation</summary><p class="rich">${renderRich(answer.explanation)}</p></details>` : ""}
        </article>
      `).join("")}
    </div>
  `;
  if (stillGrading) {
    startAttemptPolling(attemptId);
  } else {
    stopAttemptPolling();
  }
}

function renderWeakTopics(meta) {
  const weakTopics = parseJsonSafe(meta.weak_topics_json) || [];
  if (!weakTopics.length) return "";
  return `
    <div class="weak-topics">
      <strong>Review suggestions</strong>
      ${weakTopics.map((topic) => `<span class="pill">${escapeHtml(topic.topic)} · ${(topic.scoreRatio * 100).toFixed(0)}%</span>`).join("")}
    </div>
  `;
}

function startAttemptPolling(attemptId) {
  stopAttemptPolling();
  attemptPollTimer = setInterval(async () => {
    if (currentView !== "quiz" || !viewRoot.querySelector("#study-detail")) {
      stopAttemptPolling();
      return;
    }
    try {
      await renderAttempt(attemptId);
    } catch {
      stopAttemptPolling();
    }
  }, 3000);
}

function stopAttemptPolling() {
  if (attemptPollTimer) {
    clearInterval(attemptPollTimer);
    attemptPollTimer = null;
  }
}

// ---------------------------------------------------------------------------
// View: General (upload, documents, chunks, AI notes)
// ---------------------------------------------------------------------------
function renderGeneralView() {
  viewRoot.innerHTML = `
    <div class="view-header">
      <div>
        <div class="eyebrow">General</div>
        <h1>Documents & processing</h1>
      </div>
    </div>
    <div class="general-grid">
      <section class="panel">
        <div class="eyebrow">Upload</div>
        <h2>Add a PDF</h2>
        <form id="upload-form" class="form">
          <label>Title
            <input id="title" name="title" type="text" placeholder="Week 1 probability notes" />
          </label>
          <label>Document type
            <select id="document-type" name="documentType">
              <option value="COURSE_NOTES">Course notes</option>
              <option value="LECTURE_SLIDES">Lecture slides</option>
              <option value="RESEARCH_PAPER">Research paper</option>
              <option value="ASSIGNMENT">Assignment</option>
              <option value="PAST_EXAM">Past exam</option>
              <option value="HANDWRITTEN_NOTES">Handwritten notes</option>
              <option value="OTHER">Other</option>
            </select>
          </label>
          <label class="file-drop" for="pdf-file">
            <span id="file-label">Choose a PDF</span>
            <input id="pdf-file" name="file" type="file" accept="application/pdf,.pdf" required />
          </label>
          <button type="submit">Upload and Parse</button>
        </form>
      </section>
      <section class="panel">
        <div class="eyebrow">Task Status</div>
        <h2>Processing</h2>
        <div id="general-task-status" class="status-card muted">Upload a PDF to create a parsing task.</div>
      </section>
    </div>
    <section class="panel wide">
      <div class="section-header">
        <div><div class="eyebrow">Documents</div><h2>Recent uploads</h2></div>
      </div>
      <div id="general-documents" class="documents-list"></div>
    </section>
    <section class="panel wide">
      <div class="section-header">
        <div><div class="eyebrow">Document Output</div><h2>Chunks / AI Notes</h2></div>
      </div>
      <div id="parse-output" class="parse-output muted">
        Select a document above, then use View Chunks or AI Notes.
      </div>
    </section>
  `;
  const fileInput = viewRoot.querySelector("#pdf-file");
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    viewRoot.querySelector("#file-label").textContent = file ? file.name : "Choose a PDF";
  });
  renderGeneralDocuments(viewRoot.querySelector("#general-documents"), Array.from(documentsMap.values()));
  renderGeneralTaskStatus(viewRoot.querySelector("#general-task-status"));
}

function renderGeneralTaskStatus(container) {
  const activeTasks = latestTasksList.filter((t) => ["PENDING", "PROCESSING", "RETRYING"].includes(t.status));
  if (!activeTasks.length) {
    container.innerHTML = "No active tasks.";
    container.classList.add("muted");
    return;
  }
  container.classList.remove("muted");
  container.innerHTML = activeTasks.map((task) => {
    const doc = documentsMap.get(task.documentId);
    const docTitle = doc ? doc.title : task.documentId ? `Document ${task.documentId.slice(0, 8)}` : "Unknown";
    const errorHtml = task.errorMessage ? `<div class="task-error-msg">${escapeHtml(task.errorMessage)}</div>` : "";
    return `
      <div class="task-status-item">
        <div class="task-status-meta">
          <span class="task-doc-title">${escapeHtml(docTitle)}</span>
          <span class="task-type-badge">${escapeHtml(taskTypeLabel(task.taskType))}</span>
        </div>
        <div class="task-status-row">
          <div class="task-status-indicator">
            <span class="status-pulse-dot"></span>
            <span class="task-step-label">${escapeHtml(formatStepLabel(task.currentStep || task.status))}</span>
          </div>
          <span class="task-progress-pct">${task.progress}%</span>
        </div>
        ${errorHtml}
        <div class="task-progress-shell"><div class="task-progress-bar ${task.status.toLowerCase()}" style="width:${task.progress}%"></div></div>
      </div>
    `;
  }).join("");
}

function renderGeneralDocuments(container, documents) {
  if (!documents.length) {
    container.innerHTML = `<div class="status-card muted">No documents yet.</div>`;
    return;
  }
  container.innerHTML = documents.map((document) => {
    const badge = (status, readyText, pendingText, failedText, notStartedText) => {
      if (status === "READY") return `<span class="badge ready">${readyText}</span>`;
      if (["GENERATING", "PROCESSING", "PENDING", "RETRYING", "UPLOADED", "PARTIAL"].includes(status)) return `<span class="badge processing">${pendingText}</span>`;
      if (status === "FAILED") return `<span class="badge failed">${failedText}</span>`;
      return `<span class="badge muted">${notStartedText}</span>`;
    };
    return `
      <article class="document-row ${document.id === activeDocumentId ? "selected" : ""}">
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
          ${badge(document.status, "Parse Ready", "Parse Processing", "Parse Failed", "Parse Unknown")}
          ${badge(document.aiNoteStatus, "AI Note Ready", "AI Note Pending", "AI Note Failed", "AI Note Not Started")}
          ${badge(document.embeddingStatus, "Embedding Ready", "Embedding Processing", "Embedding Failed", "Embedding Not Started")}
        </div>
        <div class="row-actions">
          <button class="secondary" type="button" data-doc-select="${escapeHtml(document.id)}">${document.id === activeDocumentId ? "Selected" : "Select"}</button>
          <button class="secondary" type="button" data-view-parse="${escapeHtml(document.id)}">View Chunks</button>
          <button class="secondary" type="button" data-view-notes="${escapeHtml(document.id)}">View AI Notes</button>
          <button class="secondary" type="button" data-generate-embeddings="${escapeHtml(document.id)}">Generate Embeddings</button>
          <button type="button" data-generate-notes="${escapeHtml(document.id)}">Generate AI Notes</button>
        </div>
      </article>
    `;
  }).join("");
}

function generalOutput() {
  return viewRoot.querySelector("#parse-output");
}

function renderGeneralStatus(message, isError = false) {
  const status = viewRoot.querySelector("#general-task-status");
  if (!status) return;
  status.classList.toggle("muted", !isError);
  status.textContent = message;
}

async function handleUploadSubmit(form) {
  const fileInput = form.querySelector("#pdf-file");
  const file = fileInput.files[0];
  if (!file) {
    renderGeneralStatus("Choose a PDF first.", true);
    return;
  }
  const submitButton = form.querySelector("button[type=submit]");
  submitButton.disabled = true;
  try {
    const data = new FormData();
    data.append("file", file);
    data.append("documentType", form.querySelector("#document-type").value);
    data.append("title", form.querySelector("#title").value);
    renderGeneralStatus("Uploading PDF…");
    const response = await fetch(`${API_BASE_URL}/documents`, { method: "POST", body: data });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.message || "Upload failed");
    renderGeneralStatus(`Created document ${payload.documentId}\nCreated task ${payload.taskId}`);
  } catch (error) {
    renderGeneralStatus(formatFetchError(error), true);
  } finally {
    submitButton.disabled = false;
  }
}

async function loadParsedOutput(documentId) {
  const output = generalOutput();
  if (!output) return;
  output.classList.remove("muted");
  output.innerHTML = `<div class="output-title">Parsed Output</div><div class="status-card muted">Loading parsed output…</div>`;
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
    if (!summaryResponse.ok) throw new Error(summary.message || "Parse summary is not available yet");
    if (!chunksResponse.ok) throw new Error(chunks.message || "Chunks are not available yet");
    if (!assetsResponse.ok) throw new Error(assets.message || "Visual assets are not available yet");
    if (!blocksResponse.ok) throw new Error(blocks.message || "Layout blocks are not available yet");
    if (!regionsResponse.ok) throw new Error(regions.message || "Visual regions are not available yet");
    if (!vlmResponse.ok) throw new Error(vlmResults.message || "VLM results are not available yet");
    renderParsedOutput(
      summary, chunks, assets, blocks, regions, vlmResults,
      markdownPagesResponse.ok ? markdownPages : [],
      markdownResponse.ok ? markdownDocument : null
    );
  } catch (error) {
    output.classList.add("muted");
    output.textContent = error.message;
  }
}

async function generateNotes(documentId) {
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${documentId}/notes`, { method: "POST" });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.message || "Could not create notes task");
    renderGeneralStatus(`Created AI notes ${payload.noteId}\nCreated task ${payload.taskId}`);
    pendingNotesTasks.set(payload.taskId, documentId);
  } catch (error) {
    renderGeneralStatus(formatFetchError(error), true);
  }
}

async function generateEmbeddings(documentId) {
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${documentId}/embeddings`, { method: "POST" });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.message || "Could not create embedding task");
    renderGeneralStatus(`Embedding task ${payload.taskId}\nStatus ${payload.status}`);
  } catch (error) {
    renderGeneralStatus(formatFetchError(error), true);
  }
}

async function loadNotes(documentId) {
  const output = generalOutput();
  if (!output) return;
  output.classList.remove("muted");
  output.innerHTML = `<div class="output-title">AI Notes</div><div class="status-card muted">Loading AI notes…</div>`;
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${documentId}/notes`);
    const note = await readJson(response);
    if (!response.ok) throw new Error(note.message || "AI notes are not available yet");
    renderNotes(note);
  } catch (error) {
    output.classList.add("muted");
    output.textContent = formatFetchError(error);
  }
}

function renderNotes(note) {
  const report = parseJsonSafe(note.qualityReportJson) || {};
  const coverage = report.coveredPageStart && report.coveredPageEnd
    ? `${report.coveredPageStart}-${report.coveredPageEnd}`
    : "-";
  generalOutput().innerHTML = `
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
      <div class="rich rich-scroll">${renderRich(note.markdown || "Notes are not ready yet.")}</div>
    </div>
  `;
}

function renderParsedOutput(summary, chunks, assets, blocks, regions, vlmResults, markdownPages, markdownDocument) {
  const visualAssetCount = assets.filter((asset) => asset.visualSummary).length;
  const blockCounts = countBy(blocks, "blockType");
  const successfulVlm = vlmResults.filter((result) => result.searchText || result.description || result.transcription).length;
  const markdownQuality = markdownDocument ? parseJsonSafe(markdownDocument.qualityReportJson) : null;
  generalOutput().innerHTML = `
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
      <div class="rich rich-scroll">${renderRich(markdownDocument.markdown || "No Markdown generated.")}</div>
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
      <div class="rich rich-scroll">${renderRich(page.markdown)}</div>
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
      <div class="rich rich-scroll">${renderRich(chunk.content)}</div>
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

// ---------------------------------------------------------------------------
// View: Editor (Notion-style markdown editor with a reference pane)
//
// Left: read-only rendered blocks of the PDF Markdown or the AI note, each
// insertable into the editor at the cursor. Right: Milkdown-based WYSIWYG
// editor (math + code) persisted through /documents/{id}/editable-note with
// debounce autosave and a localStorage fallback while the API is offline.
// ---------------------------------------------------------------------------
let editorInstance = null;
let editorDocumentId = null;
let editorModulePromise = null;
let editorSaveTimer = null;
let editorDirty = false;
let editorOfflineMode = false;
let editorNoteTitle = "";
let editorOutlineVisible = localStorage.getItem("noteflowEditorOutline") === "1";
let editorStartDocumentId = localStorage.getItem("noteflowEditorStartDocument") || activeDocumentId;
let editorHomeMode = localStorage.getItem("noteflowEditorHome") === "1";
// Chunked loading for large notes: the note is split into heading-delimited
// sections; the editor always holds a PREFIX [0, editorLoadedSectionEnd) of
// them, and a scroll sentinel appends further batches. Saving composes the
// editor content with the unloaded suffix so nothing is ever lost.
let editorFullMarkdown = "";
let editorSections = [];
let editorLoadedSectionStart = 0;
let editorLoadedSectionEnd = 0;
let editorUnsavedDraft = false;
let editorDraftFolderId = null;
let editorDraftTitle = "Untitled note";
let editorLocalFolders = parseJsonSafe(localStorage.getItem("noteflowEditorLocalFolders")) || [];
let editorLocalNoteRef = null;
let editorLoadObserver = null;
let editorScrollPump = null;
let editorPumpTimer = null;
let editorAppendBusy = false;
const EDITOR_SECTION_BATCH = 30;
const EDITOR_SECTION_BYTE_BUDGET = 64000;

// Notion-ish palettes (label, css color). `null` clears the color.
const EDITOR_TEXT_COLORS = [
  ["Default", null], ["Gray", "#787774"], ["Brown", "#9F6B53"], ["Orange", "#D9730D"],
  ["Yellow", "#CB912F"], ["Green", "#448361"], ["Blue", "#337EA9"], ["Purple", "#9065B0"],
  ["Pink", "#C14C8A"], ["Red", "#D44C47"],
];
const EDITOR_BG_COLORS = [
  ["Default", null], ["Gray", "#F1F1EF"], ["Brown", "#F4EEEE"], ["Orange", "#FAEBDD"],
  ["Yellow", "#FBF3DB"], ["Green", "#EDF3EC"], ["Blue", "#E7F3F8"], ["Purple", "#F6F3F9"],
  ["Pink", "#FAF1F5"], ["Red", "#FDEBEC"],
];

const ICON_UNDO = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-4"/></svg>';
const ICON_REDO = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h4"/></svg>';

function editorLocalKey(documentId) {
  return `noteflowEditableNote:${documentId}`;
}

function loadEditorModule() {
  if (!editorModulePromise) {
    if (!document.querySelector("#noteflow-editor-css")) {
      const link = document.createElement("link");
      link.id = "noteflow-editor-css";
      link.rel = "stylesheet";
      link.href = "./vendor/editor/noteflow-editor.css";
      document.head.appendChild(link);
    }
    editorModulePromise = import("./vendor/editor/noteflow-editor.js").catch((error) => {
      editorModulePromise = null; // allow retry after a failed load
      throw error;
    });
  }
  return editorModulePromise;
}

function teardownEditor() {
  if (editorSaveTimer) {
    clearTimeout(editorSaveTimer);
    editorSaveTimer = null;
  }
  if (editorInstance && editorDirty) {
    // Fire-and-forget final save so switching views never loses edits.
    try {
      persistEditorMarkdown(editorInstance.getMarkdown());
    } catch {
      /* ignore */
    }
  }
  if (editorInstance) {
    try {
      editorInstance.destroy();
    } catch {
      /* ignore */
    }
  }
  removeEditorSentinel();
  editorInstance = null;
  editorDocumentId = null;
  editorDirty = false;
}

function persistEditorTabs() {
  localStorage.setItem("noteflowEditorTabs", JSON.stringify(editorTabs));
}

function closeEditorTab(documentId) {
  const index = editorTabs.indexOf(documentId);
  if (index === -1) return;
  editorTabs.splice(index, 1);
  if (activeDocumentId === documentId) {
    editorStartDocumentId = documentId;
    localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId);
    activeDocumentId = editorTabs[index] || editorTabs[index - 1] || null;
    localStorage.setItem("noteflowActiveDocument", activeDocumentId || "");
  }
  if (!editorTabs.length) {
    editorHomeMode = true;
    localStorage.setItem("noteflowEditorHome", "1");
  }
  persistEditorTabs();
  navigate("editor");
}

function renderEditorTabs(documents) {
  return `
    <div class="editor-tabs">
      ${editorTabs.map((id) => {
        const tabDoc = documentsMap.get(id);
        return `
          <div class="editor-tab ${!editorHomeMode && !editorUnsavedDraft && id === activeDocumentId ? "active" : ""}" data-editor-tab="${escapeHtml(id)}" title="${escapeHtml(tabDoc.title)}">
            <span class="editor-tab-title">${escapeHtml(tabDoc.title)}</span>
            <button type="button" class="editor-tab-close" data-editor-tab-close="${escapeHtml(id)}" title="Close tab">✕</button>
          </div>
        `;
      }).join("")}
      <div class="editor-tab-add">
        <button type="button" class="editor-tab-plus" data-editor-tab-create title="Upload/Create document">＋</button>
      </div>
    </div>
  `;
}

function folderNameForDocument(doc) {
  const original = doc?.originalFilename || doc?.title || "Untitled";
  return original.replace(/\.[^/.]+$/, "") || doc?.title || "Untitled";
}

function editorFolders(documents) {
  return [
    ...documents.map((doc) => ({
      id: doc.id,
      name: folderNameForDocument(doc),
      doc,
      resources: [
        { kind: "AI_NOTE", label: "AI Note", ready: doc.aiNoteStatus === "READY", meta: doc.aiNoteStatus || "Not Started" },
        { kind: "RAW", label: "PDF Markdown", ready: doc.status === "READY", meta: doc.status || "Unknown" },
        ...editorLocalNotesForFolder(doc.id),
      ],
    })),
    ...editorLocalFolders.filter((folder) => !documentsMap.has(folder.id)).map((folder) => ({
      ...folder,
      local: true,
      resources: editorLocalNotesForFolder(folder.id),
    })),
  ];
}

function editorLocalNotesForFolder(folderId) {
  const folder = editorLocalFolders.find((candidate) => candidate.id === folderId);
  return (folder?.notes || []).map((note) => ({
    kind: "LOCAL_NOTE",
    label: "Editable Note",
    ready: true,
    meta: new Date(note.updatedAt || Date.now()).toLocaleDateString(),
    note,
  }));
}

function ensureEditorLocalFolder(folderId, name) {
  let folder = editorLocalFolders.find((candidate) => candidate.id === folderId);
  if (!folder) {
    folder = { id: folderId, name, notes: [] };
    editorLocalFolders.push(folder);
  } else if (name && folder.name !== name) {
    folder.name = name;
  }
  if (!Array.isArray(folder.notes)) folder.notes = [];
  return folder;
}

function renderEditorFolderBrowser(documents, activeFolderId = editorSidebarFolderId || editorDraftFolderId || activeDocumentId || editorStartDocumentId) {
  const folders = editorFolders(documents);
  const activeFolder = folders.find((folder) => folder.id === activeFolderId) || folders[0] || null;
  return `
    <section class="editor-folder-browser">
      <aside class="editor-folder-panel">
        <div class="resource-panel-title">Folders</div>
        <div class="editor-folder-list">
          ${folders.map((folder) => `
            <button type="button" class="editor-folder-item ${activeFolder?.id === folder.id ? "active" : ""}" data-editor-folder="${escapeHtml(folder.id)}" title="${escapeHtml(folder.name)}">
              <span class="folder-icon">▣</span>
              <span>${escapeHtml(folder.name)}</span>
            </button>
          `).join("") || `<div class="side-empty">No folders yet.</div>`}
        </div>
      </aside>
      <div class="editor-folder-content">
        ${renderEditorFolderContent(activeFolder)}
      </div>
    </section>
  `;
}

function renderEditorFolderContent(activeFolder) {
  if (!activeFolder) return `<div class="side-empty">Upload a PDF in General to create folders.</div>`;
  return `
    <div class="folder-content-head">
      <div><div class="eyebrow">Folder</div><h2>${escapeHtml(activeFolder.name)}</h2></div>
    </div>
    <div class="folder-file-grid">
      ${activeFolder.resources.map((resource) => `
        <button type="button" class="folder-file-card ${resource.ready ? "" : "disabled"}" data-editor-folder-source="${escapeHtml(resource.kind)}" data-editor-folder-doc="${escapeHtml(activeFolder.id)}" ${resource.note ? `data-editor-local-note="${escapeHtml(resource.note.id)}"` : ""} ${resource.ready ? "" : "disabled"}>
          <span class="file-kind">${escapeHtml(resource.label)}</span>
          <strong>${escapeHtml(resource.note?.title || activeFolder.doc?.title || activeFolder.name)}</strong>
          <span>${resource.kind !== "LOCAL_NOTE" && activeFolder.doc?.pageCount ? `${activeFolder.doc.pageCount} pages` : escapeHtml(resource.meta)}</span>
        </button>
      `).join("") || `<div class="side-empty">No notes in this folder yet.</div>`}
    </div>
  `;
}

function persistEditorLocalFolders() {
  localStorage.setItem("noteflowEditorLocalFolders", JSON.stringify(editorLocalFolders));
}

function renderFoldersView() {
  const documents = Array.from(documentsMap.values());
  const folders = editorFolders(documents);
  if (editorSidebarFolderId && !folders.some((folder) => folder.id === editorSidebarFolderId)) {
    editorSidebarFolderId = null;
    localStorage.removeItem("noteflowEditorSidebarFolder");
  }
  if (!editorSidebarFolderId && folders[0]) {
    editorSidebarFolderId = folders[0].id;
    localStorage.setItem("noteflowEditorSidebarFolder", editorSidebarFolderId);
  }
  viewRoot.innerHTML = `
    <div class="view-header">
      <div>
        <div class="eyebrow">Folders</div>
        <h1>Resources</h1>
      </div>
    </div>
    ${folders.length ? renderEditorFolderBrowser(documents, editorSidebarFolderId) : `
      <div class="empty-view">
        <div class="chat-empty-mark">▣</div>
        <p>Upload a PDF in General to create a folder.</p>
      </div>
    `}
  `;
  wireEditorFolderBrowserEvents();
}

async function renderEditorView() {
  const documents = Array.from(documentsMap.values());
  // Drop tabs whose documents no longer exist. When the last tab is closed,
  // keep a separate start document so Start Over can re-initialize it.
  editorTabs = editorTabs.filter((id) => documentsMap.has(id));
  if (editorStartDocumentId && !documentsMap.has(editorStartDocumentId)) editorStartDocumentId = null;
  if (editorTabs.length && activeDocumentId && documentsMap.has(activeDocumentId) && !editorTabs.includes(activeDocumentId)) {
    editorTabs.push(activeDocumentId);
  }
  if (!activeDocumentId && editorTabs.length) activeDocumentId = editorTabs[0];
  if (!editorTabs.length && activeDocumentId) {
    editorStartDocumentId = activeDocumentId;
    activeDocumentId = null;
    localStorage.setItem("noteflowActiveDocument", "");
  }
  if (!editorStartDocumentId && documents.length) editorStartDocumentId = documents[0].id;
  localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId || "");
  persistEditorTabs();
  const doc = activeDocument();
  const startDoc = doc || (editorStartDocumentId ? documentsMap.get(editorStartDocumentId) : null) || null;
  const showHome = editorHomeMode || !editorTabs.length || !doc;
  if (showHome) {
    editorHomeMode = true;
    localStorage.setItem("noteflowEditorHome", "1");
    teardownEditor();
    viewRoot.innerHTML = `
      <div class="view-header editor-header">
        <div>
          <div class="eyebrow">Editor</div>
          <h1>My Notes</h1>
        </div>
      </div>
      ${renderEditorTabs(documents)}
      <section class="editor-home-page">
        <div class="editor-home-inner">
          ${renderEditorStartContent()}
        </div>
      </section>
      ${renderEditorSourceModal()}
      ${renderEditorSaveModal(documents)}
    `;
    wireEditorHomeEvents();
    return;
  }
  viewRoot.innerHTML = `
    <div class="view-header editor-header">
      <div>
        <div class="eyebrow">Editor${(doc || startDoc) ? ` · ${escapeHtml((doc || startDoc).title)}` : ""}</div>
        <h1>My Notes</h1>
      </div>
      <div class="editor-actions">
        <span id="editor-save-status" class="editor-save-status"></span>
        <button type="button" class="ghost-button" data-editor-action="save" ${!doc ? "disabled" : ""}>Save</button>
        <button type="button" class="ghost-button" data-editor-action="reinit" ${!startDoc ? "disabled" : ""}>Start over…</button>
        <button type="button" data-editor-action="export" ${!doc ? "disabled" : ""}>Export .md</button>
      </div>
    </div>
    ${renderEditorTabs(documents)}
    <div class="editor-columns ${editorOutlineVisible ? "with-outline" : ""}">
      <section class="editor-pane">
        <div id="editor-toolbar" class="editor-toolbar">
          <button type="button" class="ed-btn ed-icon" data-ed-tool="undo" title="Undo (⌘Z)">${ICON_UNDO}</button>
          <button type="button" class="ed-btn ed-icon" data-ed-tool="redo" title="Redo (⇧⌘Z)">${ICON_REDO}</button>
          <span class="ed-sep"></span>
          <div class="ed-dropdown">
            <button type="button" class="ed-btn" data-ed-menu>Turn into ▾</button>
            <div class="ed-menu" hidden>
              ${[["text", "Text"], ["h1", "Heading 1"], ["h2", "Heading 2"], ["h3", "Heading 3"], ["h4", "Heading 4"], ["bullet", "Bulleted list"], ["ordered", "Numbered list"], ["quote", "Quote"], ["code", "Code block"]]
                .map(([kind, label]) => `<button type="button" class="ed-menu-item" data-ed-turninto="${kind}">${escapeHtml(label)}</button>`)
                .join("")}
            </div>
          </div>
          <div class="ed-dropdown">
            <button type="button" class="ed-btn" data-ed-menu>Color ▾</button>
            <div class="ed-menu ed-color-menu" hidden>
              <div class="ed-menu-label">Text color</div>
              ${EDITOR_TEXT_COLORS.map(([label, value]) => `
                <button type="button" class="ed-menu-item" data-ed-color="text" data-value="${value ?? ""}">
                  <span class="ed-swatch" style="color:${value ?? "inherit"}">A</span>${escapeHtml(label)}
                </button>`).join("")}
              <div class="ed-menu-label">Background</div>
              ${EDITOR_BG_COLORS.map(([label, value]) => `
                <button type="button" class="ed-menu-item" data-ed-color="bg" data-value="${value ?? ""}">
                  <span class="ed-swatch" style="background:${value ?? "transparent"}"></span>${escapeHtml(label)}
                </button>`).join("")}
            </div>
          </div>
          <span class="ed-flex"></span>
          <button type="button" class="ed-btn ${editorOutlineVisible ? "active" : ""}" data-ed-tool="outline" title="Toggle heading outline">☰ Outline</button>
        </div>
        <div id="editor-note-shell" class="editor-note-shell"><div class="study-loading">Loading note…</div></div>
      </section>
      <aside id="editor-outline" class="editor-outline" ${editorOutlineVisible ? "" : "hidden"}>
        <div class="outline-title">Outline</div>
        <div id="editor-outline-body" class="outline-body"></div>
      </aside>
    </div>
    ${renderEditorSaveModal(documents)}
  `;
  wireEditorEvents(doc, startDoc);
  if (!editorTabs.length) {
    editorHomeMode = true;
    localStorage.setItem("noteflowEditorHome", "1");
    navigate("editor");
    return;
  }
  await loadEditorNote(doc);
}

function renderEditorStartContent() {
  return `
    <div class="editor-start">
      <h2>Start over</h2>
      <p class="editor-start-sub">Pick a starting point. Existing document tabs stay open.</p>
      <div class="editor-start-options">
        <button type="button" class="editor-start-card" data-editor-source-picker="AI_NOTE">
          <span class="start-card-title">From AI Note</span>
          <span class="start-card-sub">Choose a READY AI note to copy into the editor.</span>
        </button>
        <button type="button" class="editor-start-card" data-editor-source-picker="RAW">
          <span class="start-card-title">From PDF Markdown</span>
          <span class="start-card-sub">Choose a parsed PDF Markdown file to copy.</span>
        </button>
        <button type="button" class="editor-start-card" data-editor-blank>
          <span class="start-card-title">Blank note</span>
          <span class="start-card-sub">Start a blank note, then choose where to save it.</span>
        </button>
      </div>
      <div id="editor-start-error"></div>
    </div>
  `;
}

function renderEditorSaveModal(documents) {
  const folders = editorFolders(documents);
  return `
    <div id="editor-save-modal" class="modal-overlay" hidden>
      <div class="modal-card">
        <div class="modal-head">
          <h3>Save note</h3>
          <button type="button" class="icon-button" id="editor-save-close" title="Close">✕</button>
        </div>
        <p class="modal-sub">Choose an existing folder or create a local folder for this editable note.</p>
        <div class="modal-body editor-save-body">
          <label>Note title
            <input id="editor-save-title" type="text" value="${escapeHtml(editorDraftTitle)}" />
          </label>
          <div class="source-group-label">Existing folders</div>
          <div class="save-folder-list">
            ${folders.map((folder) => `
              <button type="button" class="save-folder-row ${folder.id === (editorDraftFolderId || activeDocumentId) ? "active" : ""}" data-save-folder="${escapeHtml(folder.id)}">
                <span>${escapeHtml(folder.name)}</span>
                <small>${folder.local ? `${folder.resources.length} notes` : escapeHtml(folder.doc.title)}</small>
              </button>
            `).join("") || `<div class="side-empty">No folders yet.</div>`}
          </div>
          <label>New folder
            <input id="editor-new-folder" type="text" placeholder="Folder name" />
          </label>
        </div>
        <div class="modal-foot">
          <button type="button" class="ghost-button" id="editor-save-cancel">Cancel</button>
          <button type="button" id="editor-save-confirm">Save</button>
        </div>
      </div>
    </div>
  `;
}

function renderEditorSourceModal() {
  const documents = Array.from(documentsMap.values());
  const readyAiNotes = documents.filter((doc) => doc.aiNoteStatus === "READY");
  const readyMarkdown = documents.filter((doc) => doc.status === "READY");
  const row = (doc) => `
    <button type="button" class="source-row source-row-button" data-editor-source-doc="${escapeHtml(doc.id)}">
      <span class="source-row-title">${escapeHtml(doc.title)}</span>
      <span class="source-row-meta">${doc.pageCount ? `${doc.pageCount} pages` : escapeHtml(doc.documentType || "")}</span>
    </button>
  `;
  return `
    <div id="editor-source-modal" class="modal-overlay" hidden>
      <div class="modal-card">
        <div class="modal-head">
          <h3 id="editor-source-title">Choose source</h3>
          <button type="button" class="icon-button" id="editor-source-close" title="Close">✕</button>
        </div>
        <p class="modal-sub" id="editor-source-sub"></p>
        <div class="modal-body">
          <div id="editor-source-ai" hidden>
            ${readyAiNotes.map(row).join("") || `<div class="side-empty">No READY AI notes.</div>`}
          </div>
          <div id="editor-source-raw" hidden>
            ${readyMarkdown.map(row).join("") || `<div class="side-empty">No READY PDF Markdown files.</div>`}
          </div>
        </div>
        <div class="modal-foot">
          <button type="button" class="ghost-button" id="editor-source-cancel">Cancel</button>
        </div>
      </div>
    </div>
  `;
}

function wireEditorHomeEvents() {
  let sourceKind = null;
  const modal = viewRoot.querySelector("#editor-source-modal");
  const openSourceModal = (kind) => {
    sourceKind = kind;
    modal.hidden = false;
    viewRoot.querySelector("#editor-source-title").textContent = kind === "AI_NOTE" ? "Choose AI Note" : "Choose PDF Markdown";
    viewRoot.querySelector("#editor-source-sub").textContent = kind === "AI_NOTE"
      ? "Pick a READY AI note to initialize an editable note."
      : "Pick a parsed PDF Markdown file to initialize an editable note.";
    viewRoot.querySelector("#editor-source-ai").hidden = kind !== "AI_NOTE";
    viewRoot.querySelector("#editor-source-raw").hidden = kind !== "RAW";
  };
  const closeSourceModal = () => {
    modal.hidden = true;
    sourceKind = null;
  };
  viewRoot.querySelector(".editor-home-page").addEventListener("click", async (event) => {
    const picker = event.target.closest("[data-editor-source-picker]");
    if (picker) {
      openSourceModal(picker.dataset.editorSourcePicker);
      return;
    }
    const blank = event.target.closest("[data-editor-blank]");
    if (blank) {
      await startBlankEditorDraft();
      return;
    }
  });
  modal.addEventListener("click", async (event) => {
    if (event.target === modal || event.target.closest("#editor-source-close") || event.target.closest("#editor-source-cancel")) {
      closeSourceModal();
      return;
    }
    const sourceDocButton = event.target.closest("[data-editor-source-doc]");
    if (!sourceDocButton || !sourceKind) return;
    const doc = documentsMap.get(sourceDocButton.dataset.editorSourceDoc);
    if (!doc) return;
    sourceDocButton.disabled = true;
    try {
      await initEditorNote(doc, sourceKind);
    } finally {
      sourceDocButton.disabled = false;
    }
  });
  wireEditorSaveModal();
}

function wireEditorFolderBrowserEvents() {
  const browser = viewRoot.querySelector(".editor-folder-browser");
  if (!browser) return;
  browser.addEventListener("click", async (event) => {
    const folderButton = event.target.closest("[data-editor-folder]");
    if (folderButton) {
      editorSidebarFolderId = folderButton.dataset.editorFolder;
      editorDraftFolderId = editorSidebarFolderId;
      localStorage.setItem("noteflowEditorSidebarFolder", editorSidebarFolderId);
      viewRoot.querySelector(".editor-folder-browser").outerHTML =
        renderEditorFolderBrowser(Array.from(documentsMap.values()), editorSidebarFolderId);
      wireEditorFolderBrowserEvents();
      return;
    }
    const folderSource = event.target.closest("[data-editor-folder-source]");
    if (!folderSource) return;
    if (folderSource.dataset.editorFolderSource === "LOCAL_NOTE") {
      const folder = editorLocalFolders.find((candidate) => candidate.id === folderSource.dataset.editorFolderDoc);
      const note = folder?.notes?.find((candidate) => candidate.id === folderSource.dataset.editorLocalNote);
      if (folder && note) await openLocalEditorNote(folder, note);
      return;
    }
    const doc = documentsMap.get(folderSource.dataset.editorFolderDoc);
    if (!doc) return;
    folderSource.disabled = true;
    try {
      await initEditorNote(doc, folderSource.dataset.editorFolderSource);
    } finally {
      folderSource.disabled = false;
    }
  });
}

async function openLocalEditorNote(folder, note) {
  currentView = "editor";
  localStorage.setItem("noteflowView", "editor");
  document.querySelectorAll("[data-nav]").forEach((button) => {
    button.classList.toggle("active", button.dataset.nav === "editor");
  });
  viewRoot.classList.add("editor-mode");
  viewRoot.classList.remove("agent-mode");
  editorHomeMode = false;
  editorUnsavedDraft = true;
  editorSidebarFolderId = null;
  editorDraftFolderId = folder.id;
  editorDraftTitle = note.title;
  editorLocalNoteRef = { folderId: folder.id, noteId: note.id };
  editorDocumentId = null;
  editorFullMarkdown = note.markdown || "";
  localStorage.setItem("noteflowEditorHome", "0");
  localStorage.removeItem("noteflowEditorSidebarFolder");
  viewRoot.innerHTML = `
    <div class="view-header editor-header">
      <div>
        <div class="eyebrow">Editor · ${escapeHtml(folder.name)}</div>
        <h1>${escapeHtml(note.title)}</h1>
      </div>
      <div class="editor-actions">
        <span id="editor-save-status" class="editor-save-status">Local</span>
        <button type="button" class="ghost-button" data-editor-action="save">Save</button>
      </div>
    </div>
    ${renderEditorTabs(Array.from(documentsMap.values()))}
    ${renderEditorEditingShell(false)}
    ${renderEditorSaveModal(Array.from(documentsMap.values()))}
  `;
  wireEditorEvents(null, null);
  wireEditorSaveModal();
  await loadEditorMarkdownSections({ id: null, title: note.title }, note.markdown || "");
}

function renderEditorEditingShell(withOutline = editorOutlineVisible) {
  return `
    <div class="editor-columns ${withOutline ? "with-outline" : ""}">
      <section class="editor-pane">
        <div id="editor-toolbar" class="editor-toolbar">
          <button type="button" class="ed-btn ed-icon" data-ed-tool="undo" title="Undo (⌘Z)">${ICON_UNDO}</button>
          <button type="button" class="ed-btn ed-icon" data-ed-tool="redo" title="Redo (⇧⌘Z)">${ICON_REDO}</button>
          <span class="ed-sep"></span>
          <div class="ed-dropdown">
            <button type="button" class="ed-btn" data-ed-menu>Turn into ▾</button>
            <div class="ed-menu" hidden>
              ${[["text", "Text"], ["h1", "Heading 1"], ["h2", "Heading 2"], ["h3", "Heading 3"], ["h4", "Heading 4"], ["bullet", "Bulleted list"], ["ordered", "Numbered list"], ["quote", "Quote"], ["code", "Code block"]]
                .map(([kind, label]) => `<button type="button" class="ed-menu-item" data-ed-turninto="${kind}">${escapeHtml(label)}</button>`)
                .join("")}
            </div>
          </div>
          <span class="ed-flex"></span>
          ${withOutline ? `<button type="button" class="ed-btn active" data-ed-tool="outline" title="Toggle heading outline">☰ Outline</button>` : ""}
        </div>
        <div id="editor-note-shell" class="editor-note-shell"><div class="study-loading">Preparing editor…</div></div>
      </section>
      ${withOutline ? `<aside id="editor-outline" class="editor-outline"><div class="outline-title">Outline</div><div id="editor-outline-body" class="outline-body"></div></aside>` : ""}
    </div>
  `;
}

function wireEditorSaveModal() {
  const modal = viewRoot.querySelector("#editor-save-modal");
  if (!modal) return;
  const close = () => { modal.hidden = true; };
  const open = () => {
    const title = modal.querySelector("#editor-save-title");
    if (title) title.value = editorDraftTitle;
    modal.hidden = false;
  };
  viewRoot.querySelectorAll("[data-editor-open-save]").forEach((button) => {
    button.addEventListener("click", open);
  });
  modal.addEventListener("click", async (event) => {
    if (event.target === modal || event.target.closest("#editor-save-close") || event.target.closest("#editor-save-cancel")) {
      close();
      return;
    }
    const folder = event.target.closest("[data-save-folder]");
    if (folder) {
      editorDraftFolderId = folder.dataset.saveFolder;
      modal.querySelectorAll(".save-folder-row").forEach((row) => row.classList.toggle("active", row === folder));
      return;
    }
    if (event.target.closest("#editor-save-confirm")) {
      await saveEditorToChosenFolder(modal);
    }
  });
}

async function startBlankEditorDraft() {
  editorHomeMode = false;
  editorUnsavedDraft = true;
  editorSidebarFolderId = null;
  editorDraftTitle = "Untitled note";
  editorLocalNoteRef = null;
  editorDocumentId = null;
  editorFullMarkdown = "";
  editorSections = splitMarkdownSections("");
  editorLoadedSectionStart = 0;
  editorLoadedSectionEnd = editorSections.length;
  localStorage.setItem("noteflowEditorHome", "0");
  localStorage.removeItem("noteflowEditorSidebarFolder");
  viewRoot.innerHTML = `
    <div class="view-header editor-header">
      <div>
        <div class="eyebrow">Editor · Unsaved</div>
        <h1>${escapeHtml(editorDraftTitle)}</h1>
      </div>
      <div class="editor-actions">
        <span id="editor-save-status" class="editor-save-status warn">Unsaved</span>
        <button type="button" class="ghost-button" data-editor-action="save">Save</button>
      </div>
    </div>
    ${renderEditorTabs(Array.from(documentsMap.values()))}
    ${renderEditorEditingShell(false)}
    ${renderEditorSaveModal(Array.from(documentsMap.values()))}
  `;
  wireEditorEvents(null, null);
  wireEditorSaveModal();
  await bootEditor({ id: null, title: editorDraftTitle }, "");
}

function wireEditorEvents(doc, startDoc = doc) {
  const toolbar = viewRoot.querySelector("#editor-toolbar");
  // Keep the editor's selection alive while clicking toolbar controls.
  toolbar.addEventListener("mousedown", (event) => event.preventDefault());
  toolbar.addEventListener("click", (event) => {
    const menuButton = event.target.closest("[data-ed-menu]");
    if (menuButton) {
      const menu = menuButton.parentElement.querySelector(".ed-menu");
      const wasHidden = menu.hidden;
      toolbar.querySelectorAll(".ed-menu").forEach((m) => { m.hidden = true; });
      menu.hidden = !wasHidden;
      return;
    }
    const turnInto = event.target.closest("[data-ed-turninto]");
    if (turnInto) {
      editorInstance?.turnInto(turnInto.dataset.edTurninto);
      toolbar.querySelectorAll(".ed-menu").forEach((m) => { m.hidden = true; });
      return;
    }
    const colorItem = event.target.closest("[data-ed-color]");
    if (colorItem) {
      const value = colorItem.dataset.value || null;
      editorInstance?.setColor(colorItem.dataset.edColor === "text" ? { color: value } : { background: value });
      toolbar.querySelectorAll(".ed-menu").forEach((m) => { m.hidden = true; });
      return;
    }
    const tool = event.target.closest("[data-ed-tool]");
    if (!tool) return;
    if (tool.dataset.edTool === "undo") editorInstance?.undo();
    if (tool.dataset.edTool === "redo") editorInstance?.redo();
    if (tool.dataset.edTool === "outline") toggleEditorOutline(tool);
  });
  viewRoot.addEventListener("click", (event) => {
    if (!event.target.closest(".ed-dropdown")) {
      toolbar.querySelectorAll(".ed-menu").forEach((m) => { m.hidden = true; });
    }
  });
  const outlineBody = viewRoot.querySelector("#editor-outline-body");
  if (outlineBody) {
    outlineBody.addEventListener("click", (event) => {
      const item = event.target.closest("[data-editor-section-index]");
      if (!item) return;
      jumpToEditorSection(Number(item.dataset.editorSectionIndex));
    });
  }
  viewRoot.querySelector(".editor-header").addEventListener("click", (event) => {
    const action = event.target.closest("[data-editor-action]");
    if (!action) return;
    if (action.dataset.editorAction === "save") {
      const modal = viewRoot.querySelector("#editor-save-modal");
      if (modal) {
        const title = modal.querySelector("#editor-save-title");
        if (title) title.value = editorDraftTitle || editorNoteTitle || activeDocument()?.title || "Untitled note";
        modal.hidden = false;
      }
    }
    if (action.dataset.editorAction === "export") exportEditorMarkdown(activeDocument() || startDoc);
    if (action.dataset.editorAction === "reinit") {
      editorHomeMode = true;
      editorSidebarFolderId = null;
      editorStartDocumentId = startDoc?.id || activeDocumentId || editorStartDocumentId;
      localStorage.setItem("noteflowEditorHome", "1");
      localStorage.removeItem("noteflowEditorSidebarFolder");
      localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId || "");
      navigate("editor");
    }
  });
}

async function loadEditorNote(doc) {
  const shell = viewRoot.querySelector("#editor-note-shell");
  if (!shell) return;
  if (!doc) return;
  editorLocalNoteRef = null;
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${doc.id}/editable-note`);
    if (response.status === 404) {
      editorHomeMode = true;
      editorStartDocumentId = doc.id;
      localStorage.setItem("noteflowEditorHome", "1");
      localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId);
      navigate("editor");
      return;
    }
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.message || "Could not load the note");
    editorOfflineMode = false;
    editorNoteTitle = payload.title || `${doc.title} - My Notes`;
    editorDraftTitle = editorNoteTitle;
    editorDraftFolderId = doc.id;
    await loadEditorMarkdownSections(doc, payload.markdown || "");
    setEditorStatus("Saved");
  } catch (error) {
    if (error instanceof TypeError) {
      // API unreachable: degrade to browser-local persistence.
      editorOfflineMode = true;
      const local = parseJsonSafe(localStorage.getItem(editorLocalKey(doc.id)));
      editorNoteTitle = local?.title || `${doc.title} - My Notes`;
      editorDraftTitle = editorNoteTitle;
      editorDraftFolderId = doc.id;
      await loadEditorMarkdownSections(doc, local?.markdown || "");
      setEditorStatus("Offline · stored in this browser", true);
      return;
    }
    shell.innerHTML = `<div class="status-card study-error">${escapeHtml(error.message || "Could not load the note")}</div>`;
  }
}

async function initEditorNote(doc, source) {
  const errorBox = viewRoot.querySelector("#editor-start-error");
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${doc.id}/editable-note`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.message || "Could not initialize the note");
    if (!editorTabs.includes(doc.id)) {
      editorTabs.push(doc.id);
      persistEditorTabs();
    }
    activeDocumentId = doc.id;
    editorStartDocumentId = doc.id;
    editorHomeMode = false;
    editorUnsavedDraft = false;
    editorLocalNoteRef = null;
    editorSidebarFolderId = null;
    editorDraftFolderId = doc.id;
    localStorage.setItem("noteflowActiveDocument", activeDocumentId);
    localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId);
    localStorage.setItem("noteflowEditorHome", "0");
    localStorage.removeItem("noteflowEditorSidebarFolder");
    navigate("editor");
    return;
  } catch (error) {
    if (errorBox) {
      errorBox.innerHTML = `<div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>`;
    }
  }
}

function splitMarkdownSections(markdown) {
  if (!markdown) {
    return [{ title: "Blank note", level: 1, start: 0, end: 0, heading: false }];
  }
  const headings = [];
  const lines = markdown.split("\n");
  let offset = 0;
  let inFence = false;
  let fenceMarker = null;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const rawLine = line + (index < lines.length - 1 ? "\n" : "");
    const fence = line.match(/^ {0,3}(```|~~~)/);
    if (fence) {
      if (!inFence) {
        inFence = true;
        fenceMarker = fence[1];
      } else if (fence[1] === fenceMarker) {
        inFence = false;
        fenceMarker = null;
      }
      offset += rawLine.length;
      continue;
    }
    if (!inFence) {
      const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) {
        headings.push({
          level: heading[1].length,
          title: heading[2].replace(/\s+#+\s*$/, "").trim() || "Untitled section",
          start: offset,
        });
      }
    }
    offset += rawLine.length;
  }
  if (!headings.length) {
    return [{ title: "Note", level: 1, start: 0, end: markdown.length, heading: false }];
  }
  const sections = [];
  if (headings[0].start > 0 && markdown.slice(0, headings[0].start).trim()) {
    sections.push({ title: "Introduction", level: 1, start: 0, end: headings[0].start, heading: false });
  }
  headings.forEach((heading, index) => {
    sections.push({
      ...heading,
      end: index + 1 < headings.length ? headings[index + 1].start : markdown.length,
      heading: true,
    });
  });
  return sections;
}

function editorMarkdownRange(start, end) {
  if (!editorSections.length) return editorFullMarkdown;
  return editorFullMarkdown.slice(editorSections[start].start, editorSections[end - 1].end);
}

async function loadEditorMarkdownSections(doc, markdown) {
  editorFullMarkdown = markdown;
  editorSections = splitMarkdownSections(markdown);
  editorLoadedSectionStart = 0;
  editorLoadedSectionEnd = nextEditorSectionEnd(0);
  await bootEditor(doc, editorMarkdownRange(editorLoadedSectionStart, editorLoadedSectionEnd));
}

// Advances from `from` by at most EDITOR_SECTION_BATCH sections or the byte
// budget, whichever is hit first (always at least one section).
function nextEditorSectionEnd(from) {
  let end = from;
  let size = 0;
  while (
    end < editorSections.length &&
    (end === from || (end - from < EDITOR_SECTION_BATCH && size < EDITOR_SECTION_BYTE_BUDGET))
  ) {
    size += editorSections[end].end - editorSections[end].start;
    end += 1;
  }
  return end;
}

function composeEditorFullMarkdown(sectionMarkdown) {
  if (!editorSections.length || editorLoadedSectionEnd <= editorLoadedSectionStart) {
    return sectionMarkdown;
  }
  const start = editorSections[editorLoadedSectionStart].start;
  const end = editorSections[editorLoadedSectionEnd - 1].end;
  return editorFullMarkdown.slice(0, start) + sectionMarkdown + editorFullMarkdown.slice(end);
}

function applyEditorSectionMarkdown(sectionMarkdown) {
  const oldStart = editorLoadedSectionStart;
  const editedSectionCount = splitMarkdownSections(sectionMarkdown).length;
  editorFullMarkdown = composeEditorFullMarkdown(sectionMarkdown);
  editorSections = splitMarkdownSections(editorFullMarkdown);
  editorLoadedSectionStart = Math.min(oldStart, Math.max(0, editorSections.length - 1));
  editorLoadedSectionEnd = Math.min(editorSections.length, editorLoadedSectionStart + Math.max(1, editedSectionCount));
}

// Appends the next batch of sections to the end of the editor document.
// editorLoadedSectionEnd advances BEFORE the append so a concurrent autosave
// composes the full markdown without duplicating the batch.
function appendNextEditorSections() {
  if (!editorInstance || editorAppendBusy) return false;
  if (editorLoadedSectionEnd >= editorSections.length) return false;
  editorAppendBusy = true;
  const from = editorLoadedSectionEnd;
  const to = nextEditorSectionEnd(from);
  const batchMarkdown = editorFullMarkdown.slice(editorSections[from].start, editorSections[to - 1].end);
  editorLoadedSectionEnd = to;
  try {
    editorInstance.appendMarkdown(batchMarkdown);
  } catch (error) {
    editorLoadedSectionEnd = from;
    console.error("Could not append note sections:", error);
    editorAppendBusy = false;
    return false;
  }
  updateEditorSentinel();
  rebuildEditorOutline();
  editorAppendBusy = false;
  return true;
}

function removeEditorSentinel() {
  if (editorLoadObserver) {
    editorLoadObserver.disconnect();
    editorLoadObserver = null;
  }
  if (editorScrollPump) {
    window.removeEventListener("scroll", editorScrollPump, true);
    editorScrollPump = null;
  }
  if (editorPumpTimer) {
    clearInterval(editorPumpTimer);
    editorPumpTimer = null;
  }
  viewRoot.querySelector("#editor-load-sentinel")?.remove();
}

function updateEditorSentinel() {
  if (editorLoadedSectionEnd >= editorSections.length) {
    removeEditorSentinel();
    return;
  }
  const sentinel = viewRoot.querySelector("#editor-load-sentinel");
  if (sentinel) {
    sentinel.textContent = `Loaded ${editorLoadedSectionEnd} / ${editorSections.length} sections — keep scrolling to load more`;
  }
}

// Keeps appending while the sentinel sits inside the preload margin, so one
// trigger drains as many batches as the viewport needs. The limit uses the
// window viewport as well as the shell so it works both when the shell is the
// scroll container and when a narrow layout degrades to page-level scrolling.
function pumpEditorSections() {
  const shell = viewRoot.querySelector("#editor-note-shell");
  const sentinel = viewRoot.querySelector("#editor-load-sentinel");
  if (!shell || !sentinel || !editorInstance) return;
  const viewportBottom = window.innerHeight || document.documentElement.clientHeight || shell.clientHeight;
  const bottomLimit = Math.min(shell.getBoundingClientRect().bottom, viewportBottom) + 800;
  if (sentinel.getBoundingClientRect().top < bottomLimit) {
    // setTimeout rather than rAF: keeps draining even in hidden tabs where
    // rAF (and IntersectionObserver) are throttled to a halt.
    if (appendNextEditorSections()) setTimeout(pumpEditorSections, 50);
  }
}

function setupEditorLazyLoad() {
  removeEditorSentinel();
  const shell = viewRoot.querySelector("#editor-note-shell");
  if (!shell || editorLoadedSectionEnd >= editorSections.length) return;
  const sentinel = document.createElement("div");
  sentinel.id = "editor-load-sentinel";
  sentinel.className = "editor-load-sentinel";
  shell.appendChild(sentinel);
  updateEditorSentinel();
  // Viewport-rooted observer: fires when the sentinel nears the visible area
  // no matter which ancestor actually scrolls (shell, page, or a degraded
  // narrow layout).
  editorLoadObserver = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) pumpEditorSections();
    },
    { rootMargin: "800px 0px" }
  );
  editorLoadObserver.observe(sentinel);
  // Scroll fallback (capture phase sees every scroll container).
  let pumpQueued = false;
  editorScrollPump = () => {
    if (pumpQueued) return;
    pumpQueued = true;
    setTimeout(() => {
      pumpQueued = false;
      pumpEditorSections();
    }, 120);
  };
  window.addEventListener("scroll", editorScrollPump, true);
  // Slow safety net: one geometry check per interval guarantees progress even
  // where both observers and scroll events are throttled away.
  editorPumpTimer = setInterval(pumpEditorSections, 1200);
  pumpEditorSections();
}

// Outline click: sections already in the editor scroll directly; unloaded ones
// are appended first, then scrolled to.
function jumpToEditorSection(sectionIndex) {
  if (!Number.isInteger(sectionIndex) || sectionIndex < 0 || sectionIndex >= editorSections.length) return;
  let guard = 0;
  while (editorLoadedSectionEnd <= sectionIndex && guard < 500) {
    if (!appendNextEditorSections()) break;
    guard += 1;
  }
  const headingOrdinal = editorSections.slice(0, sectionIndex + 1).filter((section) => section.heading).length - 1;
  const editorRoot = viewRoot.querySelector("#editor-note-shell .ProseMirror");
  if (!editorRoot) return;
  const target = headingOrdinal >= 0
    ? editorRoot.querySelectorAll("h1, h2, h3, h4, h5, h6")[headingOrdinal]
    : editorRoot.firstElementChild;
  target?.scrollIntoView({ block: "start" });
}

async function bootEditor(doc, markdown) {
  const shell = viewRoot.querySelector("#editor-note-shell");
  if (!shell) return;
  shell.innerHTML = `<div class="study-loading">Preparing editor…</div>`;
  let editorModule;
  try {
    editorModule = await loadEditorModule();
  } catch (error) {
    shell.innerHTML = `<div class="status-card study-error">Could not load the editor bundle: ${escapeHtml(error.message || "unknown error")}</div>`;
    return;
  }
  if (editorInstance) {
    try {
      editorInstance.destroy();
    } catch {
      /* ignore */
    }
    editorInstance = null;
  }
  shell.innerHTML = "";
  // Keep a skeleton visible while ProseMirror builds the first sections —
  // Crepe appends its own container, so the skeleton can sit alongside it.
  const skeleton = document.createElement("div");
  skeleton.className = "study-loading";
  skeleton.textContent = editorFullMarkdown.length > 100000
    ? `Rendering large note (${Math.round(editorFullMarkdown.length / 1024)} KB) — the first sections open now, the rest loads as you scroll…`
    : "Preparing editor…";
  shell.appendChild(skeleton);
  editorDocumentId = doc.id || null;
  editorInstance = await editorModule.createNoteFlowEditor({
    root: shell,
    defaultValue: markdown,
    onMarkdownChange: (updatedMarkdown) => scheduleEditorSave(updatedMarkdown),
  });
  skeleton.remove();
  setupEditorLazyLoad();
  rebuildEditorOutline();
}

function rebuildEditorOutline() {
  const body = viewRoot.querySelector("#editor-outline-body");
  if (!body || body.closest("#editor-outline").hidden) return;
  if (!editorSections.length) {
    body.innerHTML = `<div class="side-empty">No headings yet.</div>`;
    return;
  }
  body.innerHTML = editorSections
    .map((section, index) => `
      <button type="button" class="outline-item outline-l${Math.min(section.level || 1, 4)} ${index >= editorLoadedSectionEnd ? "tail" : ""}" data-editor-section-index="${index}" ${index >= editorLoadedSectionEnd ? 'title="Not loaded yet — click to load and jump"' : ""}>
        ${escapeHtml(section.title || "Untitled section")}
      </button>
    `)
    .join("");
}

function toggleEditorOutline(button) {
  editorOutlineVisible = !editorOutlineVisible;
  localStorage.setItem("noteflowEditorOutline", editorOutlineVisible ? "1" : "0");
  const panel = viewRoot.querySelector("#editor-outline");
  panel.hidden = !editorOutlineVisible;
  viewRoot.querySelector(".editor-columns").classList.toggle("with-outline", editorOutlineVisible);
  button.classList.toggle("active", editorOutlineVisible);
  if (editorOutlineVisible) rebuildEditorOutline();
}

function scheduleEditorSave(markdown) {
  editorDirty = true;
  setEditorStatus("Unsaved changes…");
  if (editorSaveTimer) clearTimeout(editorSaveTimer);
  editorSaveTimer = setTimeout(() => {
    editorSaveTimer = null;
    persistEditorMarkdown(markdown);
  }, 1200);
}

async function persistEditorMarkdown(markdown) {
  const documentId = editorDocumentId;
  applyEditorSectionMarkdown(markdown);
  const fullMarkdown = editorFullMarkdown;
  editorDirty = false;
  rebuildEditorOutline();
  updateEditorSentinel();
  if (!documentId) {
    if (editorLocalNoteRef) {
      const folder = editorLocalFolders.find((candidate) => candidate.id === editorLocalNoteRef.folderId);
      const note = folder?.notes?.find((candidate) => candidate.id === editorLocalNoteRef.noteId);
      if (folder && note) {
        note.title = editorDraftTitle || note.title || "Untitled note";
        note.markdown = fullMarkdown;
        note.updatedAt = new Date().toISOString();
        persistEditorLocalFolders();
        setEditorStatus(`Saved to ${folder.name}`);
        return;
      }
      editorLocalNoteRef = null;
    }
    setEditorStatus("Unsaved draft", true);
    return;
  }
  if (!editorOfflineMode) {
    try {
      const response = await fetch(`${API_BASE_URL}/documents/${documentId}/editable-note`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: editorNoteTitle, markdown: fullMarkdown }),
      });
      if (!response.ok) throw new Error("Save failed");
      setEditorStatus(`Saved · ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
      return;
    } catch {
      editorOfflineMode = true;
    }
  }
  try {
    localStorage.setItem(editorLocalKey(documentId), JSON.stringify({ title: editorNoteTitle, markdown: fullMarkdown }));
    setEditorStatus("Offline · stored in this browser", true);
  } catch {
    setEditorStatus("Could not save (storage full)", true);
  }
}

async function saveEditorToChosenFolder(modal) {
  const newFolderName = modal.querySelector("#editor-new-folder")?.value.trim();
  if (newFolderName) {
    const folder = {
      id: `local-${Date.now()}`,
      name: newFolderName,
      notes: [],
    };
    editorLocalFolders.push(folder);
    editorDraftFolderId = folder.id;
  }
  const selectedFolderId = editorDraftFolderId || activeDocumentId || Array.from(documentsMap.keys())[0];
  const selectedDoc = selectedFolderId ? documentsMap.get(selectedFolderId) : null;
  const localFolder = selectedFolderId
    ? ensureEditorLocalFolder(selectedFolderId, selectedDoc ? folderNameForDocument(selectedDoc) : "Untitled folder")
    : null;
  const markdown = editorInstance ? composeEditorFullMarkdown(editorInstance.getMarkdown()) : editorFullMarkdown;
  editorDraftTitle = modal.querySelector("#editor-save-title")?.value.trim() || "Untitled note";
  if (localFolder) {
    const existingNote = editorLocalNoteRef?.folderId === localFolder.id
      ? localFolder.notes.find((note) => note.id === editorLocalNoteRef.noteId)
      : localFolder.notes.find((note) => note.title === editorDraftTitle);
    const note = existingNote || { id: `note-${Date.now()}`, title: editorDraftTitle, markdown: "" };
    note.title = editorDraftTitle;
    note.markdown = markdown;
    note.updatedAt = new Date().toISOString();
    if (!existingNote) localFolder.notes.push(note);
    persistEditorLocalFolders();
    editorUnsavedDraft = false;
    editorLocalNoteRef = { folderId: localFolder.id, noteId: note.id };
    editorDocumentId = null;
    editorNoteTitle = editorDraftTitle;
    editorFullMarkdown = markdown;
    modal.hidden = true;
    setEditorStatus(`Saved to ${localFolder.name}`);
    return;
  }
  if (!selectedFolderId) {
    setEditorStatus("Create or upload a folder first", true);
    return;
  }
}

function setEditorStatus(text, warn = false) {
  const status = viewRoot.querySelector("#editor-save-status");
  if (!status) return;
  status.textContent = text;
  status.classList.toggle("warn", warn);
}

function exportEditorMarkdown(doc) {
  if (!doc) return;
  const markdown = editorInstance ? composeEditorFullMarkdown(editorInstance.getMarkdown()) : editorFullMarkdown;
  if (!markdown.trim()) {
    setEditorStatus("Nothing to export yet", true);
    return;
  }
  const safeName = (editorNoteTitle || `${doc.title} - My Notes`).replace(/[\\/:*?"<>|]/g, "_");
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${safeName}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  setEditorStatus("Exported .md");
}

function studyProgress(item) {
  const completed = item.completed_source_groups || 0;
  const total = item.total_source_groups || 0;
  const percent = total ? Math.round((completed / total) * 100) : item.status === "READY" ? 100 : 0;
  return `
    <div class="study-version">Version ${item.version} · ${completed}/${total || "?"} source groups</div>
    <div class="task-progress-shell"><div class="task-progress-bar ${item.status === "READY" ? "completed" : ""}" style="width:${percent}%"></div></div>
    ${item.error_message ? `<p class="study-error-text">${escapeHtml(item.error_message)}</p>` : ""}
  `;
}

function statusClass(status) {
  return status === "READY" || status === "COMPLETED" ? "ready" : status === "FAILED" ? "failed" : "processing";
}

// ---------------------------------------------------------------------------
// Event delegation
// ---------------------------------------------------------------------------
document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-nav]");
  if (nav) {
    navigate(nav.dataset.nav);
    return;
  }
  const docSelect = event.target.closest("[data-doc-select]");
  if (docSelect && !event.target.closest("[data-study-action]")) {
    selectDocument(docSelect.dataset.docSelect);
    if (currentView === "general") {
      renderGeneralDocuments(viewRoot.querySelector("#general-documents"), Array.from(documentsMap.values()));
    }
    return;
  }
  const editorTabClose = event.target.closest("[data-editor-tab-close]");
  if (editorTabClose) {
    closeEditorTab(editorTabClose.dataset.editorTabClose);
    return;
  }
  const editorTabCreate = event.target.closest("[data-editor-tab-create]");
  if (editorTabCreate) {
    const startDoc = activeDocument() || (editorStartDocumentId ? documentsMap.get(editorStartDocumentId) : null) || Array.from(documentsMap.values())[0] || null;
    if (startDoc) {
      editorStartDocumentId = startDoc.id;
      localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId);
    }
    editorHomeMode = true;
    editorSidebarFolderId = null;
    localStorage.setItem("noteflowEditorHome", "1");
    localStorage.removeItem("noteflowEditorSidebarFolder");
    navigate("editor");
    return;
  }
  const editorTab = event.target.closest("[data-editor-tab]");
  if (editorTab) {
    activeDocumentId = editorTab.dataset.editorTab;
    editorHomeMode = false;
    editorUnsavedDraft = false;
    editorSidebarFolderId = null;
    localStorage.setItem("noteflowActiveDocument", activeDocumentId);
    localStorage.setItem("noteflowEditorHome", "0");
    localStorage.removeItem("noteflowEditorSidebarFolder");
    navigate("editor");
    return;
  }
  const refreshView = event.target.closest("[data-refresh-view]");
  if (refreshView) {
    navigate(refreshView.dataset.refreshView);
    return;
  }
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
  const studyAction = event.target.closest("[data-study-action]");
  if (studyAction) {
    await handleStudyAction(studyAction);
  }
});

document.addEventListener("input", (event) => {
  if (!event.target.closest(".quiz-options")) return;
  const total = ["#quiz-easy", "#quiz-medium", "#quiz-hard"]
    .reduce((sum, id) => sum + Math.max(0, Number(viewRoot.querySelector(id)?.value || 0)), 0);
  const label = viewRoot.querySelector("#quiz-total");
  if (label) label.textContent = `Total: ${total}`;
});

document.addEventListener("submit", async (event) => {
  const upload = event.target.closest("#upload-form");
  if (upload) {
    event.preventDefault();
    await handleUploadSubmit(upload);
    return;
  }
  const chat = event.target.closest("#chat-form");
  if (chat) {
    event.preventDefault();
    await handleChatSubmit(chat);
    return;
  }
  const quizForm = event.target.closest("#quiz-attempt-form");
  if (quizForm) {
    event.preventDefault();
    const submit = quizForm.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await submitQuizAttempt(quizForm);
    } catch (error) {
      viewRoot.querySelector("#study-detail")?.insertAdjacentHTML(
        "afterbegin",
        `<div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>`
      );
      submit.disabled = false;
    }
  }
});

async function handleStudyAction(action) {
  const kind = action.dataset.studyAction;
  try {
    action.disabled = true;
    // Per-file generate buttons on the right panel carry data-doc-id; the main
    // module's generate button falls back to the currently selected document.
    if (kind === "generate-cards") {
      const docId = action.dataset.docId || activeDocumentId;
      if (!docId) throw new Error("Pick a file first.");
      activeDocumentId = docId;
      localStorage.setItem("noteflowActiveDocument", docId);
      await studyPost(`/documents/${docId}/flashcard-decks`);
      navigate("flashcards");
    }
    if (kind === "generate-quiz") {
      const docId = action.dataset.docId || activeDocumentId;
      if (!docId) throw new Error("Pick a file first.");
      // Difficulty inputs only exist when the main module shows this document;
      // panel-button generation for another file uses the 3/5/2 defaults.
      const readCount = (id, fallback) => {
        const input = viewRoot.querySelector(id);
        return input ? Math.max(0, Number(input.value || 0)) : fallback;
      };
      const body = {
        easy: readCount("#quiz-easy", 3),
        medium: readCount("#quiz-medium", 5),
        hard: readCount("#quiz-hard", 2),
      };
      if (body.easy + body.medium + body.hard < 1) {
        throw new Error("Choose at least one question across the difficulty levels.");
      }
      activeDocumentId = docId;
      localStorage.setItem("noteflowActiveDocument", docId);
      await studyPost(`/documents/${docId}/quiz-sets`, body);
      navigate("quiz");
    }
    if (kind === "review") await renderReview(action.dataset.deckId);
    if (kind === "browse-cards") await renderCardBrowser(action.dataset.deckId);
    if (kind === "start-quiz") await startQuiz(action.dataset.quizId);
    if (kind === "grade-card") {
      await studyPost(`/flashcards/${action.dataset.cardId}/reviews`, { grade: action.dataset.grade });
      await renderReview(action.dataset.deckId);
    }
    if (kind === "view-result") await renderAttempt(action.dataset.attemptId);
  } catch (error) {
    const detail = viewRoot.querySelector("#study-detail") || viewRoot;
    detail.insertAdjacentHTML("afterbegin", `<div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>`);
  } finally {
    action.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Shared utilities
// ---------------------------------------------------------------------------
async function refreshDocumentsOnce() {
  try {
    const response = await fetch(`${API_BASE_URL}/documents`);
    const documents = await readJson(response);
    if (!response.ok) throw new Error(documents.message || "Could not load documents");
    documentsMap = new Map(documents.map((d) => [d.id, d]));
  } catch (error) {
    console.error("Could not load documents:", error);
  }
}

async function studyPost(path, body) { return studyRequest(path, "POST", body); }
async function studyPut(path, body) { return studyRequest(path, "PUT", body); }
async function requestJson(path, method = "GET", body) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await readJson(response);
  if (!response.ok) throw new Error(payload.message || "Request failed");
  return payload;
}
async function studyRequest(path, method, body) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await readJson(response);
  if (!response.ok) throw new Error(payload.message || "Study request failed");
  return payload;
}

async function readJson(response) {
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function parseJsonSafe(value) {
  if (!value) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function countBy(items, key) {
  return items.reduce((counts, item) => {
    const value = item[key] || "UNKNOWN";
    counts[value] = (counts[value] || 0) + 1;
    return counts;
  }, {});
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
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

function summaryItem(label, value) {
  return `
    <div class="summary-item">
      <div class="summary-label">${escapeHtml(label)}</div>
      <div class="summary-value">${escapeHtml(value ?? "-")}</div>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

// ---------------------------------------------------------------------------
// Rich rendering: Markdown + LaTeX (KaTeX)
//
// Math spans are extracted BEFORE escaping so that `<`, `>`, `&`, and `\`
// inside TeX survive intact, then rendered with katex.renderToString and
// re-inserted. The Markdown subset is deliberately small and operates on
// already-escaped text, so no untrusted HTML can reach the DOM.
// ---------------------------------------------------------------------------
function renderRich(rawText) {
  if (rawText == null || rawText === "") return "";
  const text = String(rawText);
  const mathTokens = [];
  const protectedText = protectMath(text, mathTokens);
  let html = renderMarkdownSubset(protectedText);
  // Control-char sentinels never occur in study content and survive HTML
  // escaping intact, so placeholders cannot collide with text like "MATH239".
  html = html.replace(/\u0001(\d+)\u0001/g, (_, index) => renderMathToken(mathTokens[Number(index)]));
  return html;
}

function protectMath(text, tokens) {
  const patterns = [
    { re: /\$\$([\s\S]+?)\$\$/g, display: true },
    { re: /\\\[([\s\S]+?)\\\]/g, display: true },
    { re: /\\\(([\s\S]+?)\\\)/g, display: false },
    { re: /(?<![\\$])\$(?!\s)((?:\\.|[^$\\])+?)(?<!\s)\$(?!\d)/g, display: false },
  ];
  let output = text;
  for (const { re, display } of patterns) {
    output = output.replace(re, (_, tex) => {
      const index = tokens.push({ tex: tex.trim(), display }) - 1;
      return `\u0001${index}\u0001`;
    });
  }
  return output;
}

function renderMathToken(token) {
  if (!token) return "";
  if (typeof window.katex === "undefined") {
    return `<code class="math-fallback">${escapeHtml(token.tex)}</code>`;
  }
  try {
    return window.katex.renderToString(token.tex, {
      displayMode: token.display,
      throwOnError: false,
      output: "html",
    });
  } catch {
    return `<code class="math-fallback">${escapeHtml(token.tex)}</code>`;
  }
}

function renderMarkdownSubset(text) {
  const lines = text.split("\n");
  const blocks = [];
  let paragraph = [];
  let list = null;
  let code = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push(`<p>${paragraph.map(renderInline).join("<br/>")}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      const tag = list.ordered ? "ol" : "ul";
      blocks.push(`<${tag}>${list.items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</${tag}>`);
      list = null;
    }
  };

  for (const rawLine of lines) {
    if (code) {
      if (rawLine.trim().startsWith("```")) {
        blocks.push(`<pre class="code-block"><code>${code.lines.join("\n")}</code></pre>`);
        code = null;
      } else {
        code.lines.push(escapeHtml(rawLine));
      }
      continue;
    }
    const line = rawLine.replace(/\s+$/, "");
    const fenceMatch = line.trim().match(/^```(\w*)/);
    if (fenceMatch) {
      flushParagraph();
      flushList();
      code = { lang: fenceMatch[1], lines: [] };
      continue;
    }
    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      const level = headingMatch[1].length;
      blocks.push(`<h${level} class="md-h md-h${level}">${renderInline(headingMatch[2])}</h${level}>`);
      continue;
    }
    const orderedMatch = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const bulletMatch = line.match(/^\s*[-*+]\s+(.*)$/);
    if (orderedMatch || bulletMatch) {
      flushParagraph();
      const ordered = Boolean(orderedMatch);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((orderedMatch || bulletMatch)[1]);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  if (code) blocks.push(`<pre class="code-block"><code>${code.lines.join("\n")}</code></pre>`);
  flushParagraph();
  flushList();
  return blocks.join("");
}

function renderInline(text) {
  const codeSpans = [];
  let out = text.replace(/`([^`]+)`/g, (_, code) => {
    const index = codeSpans.push(code) - 1;
    return `\u0002${index}\u0002`;
  });
  out = escapeHtml(out);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
  out = out.replace(/\u0002(\d+)\u0002/g, (_, index) => `<code>${escapeHtml(codeSpans[Number(index)])}</code>`);
  return out;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
(async function boot() {
  await refreshDocumentsOnce();
  navigate(currentView);
  await startGlobalPolling();
})();
