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
const sidebarDocuments = document.querySelector("#sidebar-documents");
const sidebarTasks = document.querySelector("#sidebar-tasks");

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
const VIEWS = {
  agent: renderAgentView,
  editor: renderEditorView,
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
  VIEWS[view]();
}

function activeDocument() {
  return activeDocumentId ? documentsMap.get(activeDocumentId) : null;
}

function selectDocument(documentId) {
  activeDocumentId = documentId;
  localStorage.setItem("noteflowActiveDocument", documentId || "");
  renderSidebarDocuments();
  if (currentView === "flashcards" || currentView === "quiz" || currentView === "editor") {
    navigate(currentView);
  } else if (currentView === "agent") {
    const hint = viewRoot.querySelector("#chat-scope-hint");
    if (hint) hint.textContent = scopeHintText();
  } else {
    renderSidebarDocuments();
  }
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------
function renderSidebarDocuments() {
  const documents = Array.from(documentsMap.values());
  if (!documents.length) {
    sidebarDocuments.innerHTML = `<div class="side-empty">No documents yet.<br/>Upload one in General.</div>`;
    return;
  }
  sidebarDocuments.innerHTML = documents
    .map((doc) => {
      const dotClass = doc.status === "READY" ? "ready" : doc.status === "FAILED" ? "failed" : "processing";
      return `
        <button type="button" class="side-doc ${doc.id === activeDocumentId ? "active" : ""}" data-doc-select="${escapeHtml(doc.id)}" title="${escapeHtml(doc.title)}">
          <span class="doc-dot ${dotClass}"></span>
          <span class="side-doc-title">${escapeHtml(doc.title)}</span>
        </button>
      `;
    })
    .join("");
}

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
        renderSidebarDocuments();
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
function scopeHintText() {
  const doc = activeDocument();
  return doc ? `Scoped to: ${doc.title}` : "Scope: all documents";
}

function renderAgentView() {
  const documents = Array.from(documentsMap.values());
  viewRoot.innerHTML = `
    <div class="view-header">
      <div>
        <div class="eyebrow">AI Agent</div>
        <h1>Ask your study material</h1>
      </div>
    </div>
    <div class="chat-shell">
      <div class="chat-banner">
        Answers are grounded in retrieved evidence from your PDFs and AI notes with page-level citations.
      </div>
      <div id="chat-messages" class="chat-messages">
        ${chatMessages.length ? chatMessages.map(renderChatMessage).join("") : `
          <div class="chat-empty">
            <div class="chat-empty-mark">✦</div>
            <p>Ask about a theorem, formula, proof step, or code snippet.<br/>Sources are cited with document and page.</p>
          </div>`}
      </div>
      <form id="chat-form" class="chat-composer">
        <div class="chat-controls">
          <label>Scope
            <select id="chat-doc-scope">
              <option value="ALL">All documents</option>
              <option value="ACTIVE" ${activeDocumentId ? "" : "disabled"}>Selected document</option>
              <option value="CUSTOM">Custom selection</option>
            </select>
          </label>
          <label>Sources
            <select id="chat-mode">
              <option value="MIXED">PDF + AI Notes</option>
              <option value="PDF">PDF only</option>
              <option value="AI_NOTE">AI Notes only</option>
            </select>
          </label>
          <span id="chat-scope-hint" class="chat-scope-hint">${escapeHtml(scopeHintText())}</span>
        </div>
        <div id="chat-custom-scope" class="custom-search-scope" hidden>
          ${renderCustomSearchScope(documents)}
        </div>
        <div class="chat-input-row">
          <input id="chat-query" type="text" placeholder="Ask anything about your documents…" autocomplete="off" required />
          <button type="submit">Send</button>
        </div>
      </form>
    </div>
  `;
  const scopeSelect = viewRoot.querySelector("#chat-doc-scope");
  const customScope = viewRoot.querySelector("#chat-custom-scope");
  scopeSelect.addEventListener("change", () => {
    customScope.hidden = scopeSelect.value !== "CUSTOM";
  });
  scrollChatToBottom();
  viewRoot.querySelector("#chat-query").focus();
  if (activeConversationId && !conversationHydrated && !chatMessages.length) hydrateConversation();
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
  const scope = form.querySelector("#chat-doc-scope").value;
  const mode = form.querySelector("#chat-mode").value;
  const sourceScope = conversationSourceScope(form, scope, mode);
  if (scope === "CUSTOM" && !sourceScope.pdfDocumentIds.length && !sourceScope.aiNoteDocumentIds.length) {
      pushAssistantMessage(`<p class="chat-note">Choose at least one PDF or AI Note for a custom scope.</p>`);
      return;
  }

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

function conversationSourceScope(form, scope, mode) {
  let pdfDocumentIds = [];
  let aiNoteDocumentIds = [];
  if (scope === "CUSTOM") {
    pdfDocumentIds = checkedValues(form, "pdfDocumentIds");
    aiNoteDocumentIds = checkedValues(form, "aiNoteDocumentIds");
  } else if (scope === "ACTIVE" && activeDocumentId) {
    pdfDocumentIds = [activeDocumentId];
    aiNoteDocumentIds = [activeDocumentId];
  } else if (mode !== "MIXED") {
    const allIds = Array.from(documentsMap.keys());
    pdfDocumentIds = mode === "PDF" ? allIds : [];
    aiNoteDocumentIds = mode === "AI_NOTE" ? allIds : [];
  }
  if (mode === "PDF") aiNoteDocumentIds = [];
  if (mode === "AI_NOTE") pdfDocumentIds = [];
  return { pdfDocumentIds, aiNoteDocumentIds };
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

function pushAssistantMessage(html) {
  chatMessages.push({ role: "assistant", html });
  refreshChatMessages();
}

function renderEvidenceAnswer(payload, query) {
  const results = payload.results || [];
  if (!results.length) {
    return `<p class="chat-note">No matching embedded source was found for “${escapeHtml(query)}”. Generate embeddings in General, or broaden the scope.</p>`;
  }
  return `
    <p class="chat-answer-head">Top ${results.length} source${results.length > 1 ? "s" : ""} for “${escapeHtml(query)}”:</p>
    <div class="chat-evidence">${results.map(renderSearchResult).join("")}</div>
  `;
}

function renderCustomSearchScope(documents) {
  if (!documents.length) {
    return `<div class="status-card muted">No documents are available for custom scope.</div>`;
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
      <p class="rich">${renderRich(result.snippet || "No preview available.")}</p>
    </article>
  `;
}

// ---------------------------------------------------------------------------
// View: Flashcards
// ---------------------------------------------------------------------------
async function renderFlashcardsView() {
  const doc = activeDocument();
  if (!doc) {
    viewRoot.innerHTML = viewNeedsDocument("Flashcards", "Pick a document in the sidebar to see its flashcard decks.");
    return;
  }
  viewRoot.innerHTML = viewLoading("Flashcards", doc.title);
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${doc.id}/flashcard-decks`);
    const decks = await readJson(response);
    if (!response.ok) throw new Error(decks.message || "Could not load flashcard decks");
    const deck = decks[0];
    viewRoot.innerHTML = `
      <div class="view-header">
        <div>
          <div class="eyebrow">Flashcards · ${escapeHtml(doc.title)}</div>
          <h1>Spaced repetition</h1>
        </div>
        <button class="secondary" type="button" data-refresh-view="flashcards">Refresh</button>
      </div>
      <article class="study-module flashcard-module">
        <div class="study-module-head">
          <div><span class="study-icon">▣</span><h3>Deck</h3></div>
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
      <div id="study-detail" class="study-detail"></div>
    `;
  } catch (error) {
    viewRoot.innerHTML = viewError("Flashcards", error);
  }
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
  if (!doc) {
    viewRoot.innerHTML = viewNeedsDocument("Quiz", "Pick a document in the sidebar to see its quizzes.");
    return;
  }
  viewRoot.innerHTML = viewLoading("Quiz", doc.title);
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${doc.id}/quiz-sets`);
    const quizzes = await readJson(response);
    if (!response.ok) throw new Error(quizzes.message || "Could not load quizzes");
    const quiz = quizzes[0];
    viewRoot.innerHTML = `
      <div class="view-header">
        <div>
          <div class="eyebrow">Quiz · ${escapeHtml(doc.title)}</div>
          <h1>Practice and grading</h1>
        </div>
        <button class="secondary" type="button" data-refresh-view="quiz">Refresh</button>
      </div>
      <article class="study-module quiz-module">
        <div class="study-module-head">
          <div><span class="study-icon">?</span><h3>Quiz set</h3></div>
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
      <div id="study-detail" class="study-detail"></div>
    `;
  } catch (error) {
    viewRoot.innerHTML = viewError("Quiz", error);
  }
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
  editorInstance = null;
  editorDocumentId = null;
  editorDirty = false;
}

async function renderEditorView() {
  const doc = activeDocument();
  if (!doc) {
    viewRoot.innerHTML = viewNeedsDocument("Editor", "Select a document in the sidebar to start writing notes.");
    return;
  }
  viewRoot.innerHTML = `
    <div class="view-header editor-header">
      <div>
        <div class="eyebrow">Editor · ${escapeHtml(doc.title)}</div>
        <h1>My Notes</h1>
      </div>
      <div class="editor-actions">
        <span id="editor-save-status" class="editor-save-status"></span>
        <button type="button" class="ghost-button" data-editor-action="reinit">Start over…</button>
        <button type="button" data-editor-action="export">Export .md</button>
      </div>
    </div>
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
  `;
  wireEditorEvents(doc);
  await loadEditorNote(doc);
}

function wireEditorEvents(doc) {
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
  viewRoot.querySelector("#editor-outline-body").addEventListener("click", (event) => {
    const item = event.target.closest("[data-outline-index]");
    if (!item) return;
    const headings = editorHeadings();
    const heading = headings[Number(item.dataset.outlineIndex)];
    if (heading) heading.scrollIntoView({ block: "start" });
  });
  viewRoot.querySelector(".editor-header").addEventListener("click", (event) => {
    const action = event.target.closest("[data-editor-action]");
    if (!action) return;
    if (action.dataset.editorAction === "export") exportEditorMarkdown(doc);
    if (action.dataset.editorAction === "reinit") renderEditorStart(doc, true);
  });
  viewRoot.querySelector("#editor-note-shell").addEventListener("click", async (event) => {
    const init = event.target.closest("[data-editor-init]");
    if (!init) return;
    init.disabled = true;
    try {
      await initEditorNote(doc, init.dataset.editorInit);
    } finally {
      const button = viewRoot.querySelector(`[data-editor-init="${init.dataset.editorInit}"]`);
      if (button) button.disabled = false;
    }
  });
}

async function loadEditorNote(doc) {
  const shell = viewRoot.querySelector("#editor-note-shell");
  if (!shell) return;
  try {
    const response = await fetch(`${API_BASE_URL}/documents/${doc.id}/editable-note`);
    if (response.status === 404) {
      renderEditorStart(doc);
      return;
    }
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.message || "Could not load the note");
    editorOfflineMode = false;
    editorNoteTitle = payload.title || `${doc.title} - My Notes`;
    await bootEditor(doc, payload.markdown || "");
    setEditorStatus("Saved");
  } catch (error) {
    if (error instanceof TypeError) {
      // API unreachable: degrade to browser-local persistence.
      editorOfflineMode = true;
      const local = parseJsonSafe(localStorage.getItem(editorLocalKey(doc.id)));
      editorNoteTitle = local?.title || `${doc.title} - My Notes`;
      await bootEditor(doc, local?.markdown || "");
      setEditorStatus("Offline · stored in this browser", true);
      return;
    }
    shell.innerHTML = `<div class="status-card study-error">${escapeHtml(error.message || "Could not load the note")}</div>`;
  }
}

function renderEditorStart(doc, isReset = false) {
  const shell = viewRoot.querySelector("#editor-note-shell");
  if (!shell) return;
  if (editorInstance) {
    try {
      editorInstance.destroy();
    } catch {
      /* ignore */
    }
    editorInstance = null;
    editorDocumentId = null;
  }
  const aiNoteReady = doc.aiNoteStatus === "READY";
  shell.innerHTML = `
    <div class="editor-start">
      <h2>${isReset ? "Start over" : "Create your note"}</h2>
      <p class="editor-start-sub">${isReset
        ? "Re-initializing replaces the current note content. The PDF Markdown and AI note sources stay untouched."
        : "Pick a starting point. You can edit freely afterwards; the original sources stay untouched."}</p>
      <div class="editor-start-options">
        <button type="button" class="editor-start-card" data-editor-init="AI_NOTE" ${aiNoteReady ? "" : "disabled"}>
          <span class="start-card-title">From AI Note</span>
          <span class="start-card-sub">${aiNoteReady ? "Copy the latest READY AI note into your editable note." : "No READY AI note for this document yet."}</span>
        </button>
        <button type="button" class="editor-start-card" data-editor-init="RAW">
          <span class="start-card-title">From PDF Markdown</span>
          <span class="start-card-sub">Copy the full parsed Markdown of the PDF.</span>
        </button>
        <button type="button" class="editor-start-card" data-editor-init="BLANK">
          <span class="start-card-title">Blank note</span>
          <span class="start-card-sub">Start from an empty page and insert blocks from the left.</span>
        </button>
      </div>
      <div id="editor-start-error"></div>
    </div>
  `;
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
    editorOfflineMode = false;
    editorNoteTitle = payload.title || `${doc.title} - My Notes`;
    await bootEditor(doc, payload.markdown || "");
    setEditorStatus("Saved");
  } catch (error) {
    if (errorBox) {
      errorBox.innerHTML = `<div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>`;
    }
  }
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
  editorDocumentId = doc.id;
  editorInstance = await editorModule.createNoteFlowEditor({
    root: shell,
    defaultValue: markdown,
    onMarkdownChange: (updatedMarkdown) => scheduleEditorSave(updatedMarkdown),
  });
  rebuildEditorOutline();
}

function editorHeadings() {
  return Array.from(viewRoot.querySelectorAll("#editor-note-shell .ProseMirror h1, #editor-note-shell .ProseMirror h2, #editor-note-shell .ProseMirror h3, #editor-note-shell .ProseMirror h4"));
}

function rebuildEditorOutline() {
  const body = viewRoot.querySelector("#editor-outline-body");
  if (!body || body.closest("#editor-outline").hidden) return;
  const headings = editorHeadings();
  if (!headings.length) {
    body.innerHTML = `<div class="side-empty">No headings yet.</div>`;
    return;
  }
  body.innerHTML = headings
    .map((heading, index) => `
      <button type="button" class="outline-item outline-l${heading.tagName.slice(1)}" data-outline-index="${index}">
        ${escapeHtml(heading.textContent.trim() || "(empty heading)")}
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
  rebuildEditorOutline();
  if (editorSaveTimer) clearTimeout(editorSaveTimer);
  editorSaveTimer = setTimeout(() => {
    editorSaveTimer = null;
    persistEditorMarkdown(markdown);
  }, 1200);
}

async function persistEditorMarkdown(markdown) {
  const documentId = editorDocumentId;
  if (!documentId) return;
  editorDirty = false;
  if (!editorOfflineMode) {
    try {
      const response = await fetch(`${API_BASE_URL}/documents/${documentId}/editable-note`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: editorNoteTitle, markdown }),
      });
      if (!response.ok) throw new Error("Save failed");
      setEditorStatus(`Saved · ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
      return;
    } catch {
      editorOfflineMode = true;
    }
  }
  try {
    localStorage.setItem(editorLocalKey(documentId), JSON.stringify({ title: editorNoteTitle, markdown }));
    setEditorStatus("Offline · stored in this browser", true);
  } catch {
    setEditorStatus("Could not save (storage full)", true);
  }
}

function setEditorStatus(text, warn = false) {
  const status = viewRoot.querySelector("#editor-save-status");
  if (!status) return;
  status.textContent = text;
  status.classList.toggle("warn", warn);
}

function exportEditorMarkdown(doc) {
  const markdown = editorInstance ? editorInstance.getMarkdown() : "";
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

// ---------------------------------------------------------------------------
// View helpers
// ---------------------------------------------------------------------------
function viewNeedsDocument(title, message) {
  return `
    <div class="view-header"><div><div class="eyebrow">${escapeHtml(title)}</div><h1>${escapeHtml(title)}</h1></div></div>
    <div class="empty-view">
      <div class="chat-empty-mark">▤</div>
      <p>${escapeHtml(message)}</p>
    </div>
  `;
}

function viewLoading(title, docTitle) {
  return `
    <div class="view-header"><div><div class="eyebrow">${escapeHtml(title)} · ${escapeHtml(docTitle)}</div><h1>${escapeHtml(title)}</h1></div></div>
    <div class="study-loading">Loading…</div>
  `;
}

function viewError(title, error) {
  return `
    <div class="view-header"><div><div class="eyebrow">${escapeHtml(title)}</div><h1>${escapeHtml(title)}</h1></div></div>
    <div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>
  `;
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
  if (docSelect) {
    selectDocument(docSelect.dataset.docSelect);
    if (currentView === "general") {
      renderGeneralDocuments(viewRoot.querySelector("#general-documents"), Array.from(documentsMap.values()));
    }
    return;
  }
  if (event.target.closest("#sidebar-refresh")) {
    await refreshDocumentsOnce();
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
    if (kind === "generate-cards") {
      await studyPost(`/documents/${activeDocumentId}/flashcard-decks`);
      navigate("flashcards");
    }
    if (kind === "generate-quiz") {
      const readCount = (id) => Math.max(0, Number(viewRoot.querySelector(id)?.value || 0));
      const body = {
        easy: readCount("#quiz-easy"),
        medium: readCount("#quiz-medium"),
        hard: readCount("#quiz-hard"),
      };
      if (body.easy + body.medium + body.hard < 1) {
        throw new Error("Choose at least one question across the difficulty levels.");
      }
      await studyPost(`/documents/${activeDocumentId}/quiz-sets`, body);
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
    renderSidebarDocuments();
  } catch (error) {
    sidebarDocuments.innerHTML = `<div class="side-empty">${escapeHtml(formatFetchError(error))}</div>`;
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

function checkedValues(formEl, name) {
  return Array.from(formEl.querySelectorAll(`input[name="${name}"]:checked`)).map((input) => input.value);
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
