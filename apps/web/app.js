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
let conversationsList = [];
let conversationsLoaded = false;
let conversationsLoading = false;
let conversationsError = null;
let attemptPollTimer = null;
let globalPollTimeout = null;

const viewRoot = document.querySelector("#view-root");
const sidebarTasks = document.querySelector("#sidebar-tasks");

// Per-module selection UIs replaced the old shared sidebar document list:
// - AI Agent: "+ Sources" modal (multi-select across Markdown / AI Notes)
// - Flashcards & Quiz: right-side file panel with per-file generate buttons
// - Editor: top document tabs
let agentSources = parseJsonSafe(localStorage.getItem("noteflowAgentSources")) || { pdf: [], aiNote: [] };
let editorTabs = parseJsonSafe(localStorage.getItem("noteflowEditorTabs")) || [];

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
  settings: renderSettingsView,
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
  if (globalPollTimeout) clearTimeout(globalPollTimeout);
  const tick = async () => {
    if (document.hidden) {
      globalPollTimeout = setTimeout(tick, 5000);
      return;
    }
    // Back off while the API is unreachable and log the outage once instead of
    // flooding the console.
    if (pollFailureCount >= 3 && pollFailureCount % 7 !== 0) {
      pollFailureCount += 1;
      globalPollTimeout = setTimeout(tick, 5000);
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
    const hasActiveTasks = latestTasksList.some((task) => ["PENDING", "PROCESSING", "RETRYING"].includes(task.status));
    globalPollTimeout = setTimeout(tick, hasActiveTasks ? 1500 : 5000);
  };
  await tick();
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
    <div class="agent-layout">
      <aside class="conversation-panel">
        <div class="conversation-panel-head">
          <strong>Conversations</strong>
          <button type="button" class="icon-button" data-conversation-new title="New chat">＋</button>
        </div>
        <div id="conversation-list" class="conversation-list">
          ${renderConversationList()}
        </div>
      </aside>
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
    </div>
    ${renderSourceModal()}
  `;
  wireSourceModal();
  scrollChatToBottom();
  viewRoot.querySelector("#chat-query").focus();
  if (!conversationsLoaded && !conversationsLoading) loadConversationList({ hydrateLatestIfEmpty: true });
  if (activeConversationId && !conversationHydrated && !chatMessages.length) hydrateConversation(activeConversationId);
}

function renderConversationList() {
  if (conversationsLoading && !conversationsList.length) {
    return `<div class="side-empty">Loading conversations…</div>`;
  }
  if (conversationsError) {
    return `<div class="side-empty">${escapeHtml(conversationsError)}</div>`;
  }
  if (!conversationsList.length) {
    return `<div class="side-empty">No conversations yet.</div>`;
  }
  return conversationsList.map((conversation) => {
    const id = String(conversation.id);
    const title = conversation.title || "New conversation";
    const timestamp = conversation.last_message_at || conversation.updated_at || conversation.created_at;
    return `
      <button type="button" class="conversation-row ${id === activeConversationId ? "active" : ""}" data-conversation-id="${escapeHtml(id)}" title="${escapeHtml(title)}">
        <span class="conversation-title">${escapeHtml(title)}</span>
        <span class="conversation-time">${escapeHtml(formatConversationTime(timestamp))}</span>
      </button>
    `;
  }).join("");
}

function refreshConversationList() {
  const list = viewRoot.querySelector("#conversation-list");
  if (list) list.innerHTML = renderConversationList();
}

async function loadConversationList(options = {}) {
  conversationsLoading = true;
  conversationsError = null;
  refreshConversationList();
  try {
    conversationsList = await requestJson("/conversations");
    conversationsLoaded = true;
    if (!activeConversationId && options.hydrateLatestIfEmpty && conversationsList.length) {
      activeConversationId = String(conversationsList[0].id);
      localStorage.setItem("noteflowConversationId", activeConversationId);
      conversationHydrated = false;
    }
  } catch (error) {
    conversationsError = formatFetchError(error);
  } finally {
    conversationsLoading = false;
    refreshConversationList();
  }
  if (currentView === "agent" && activeConversationId && !conversationHydrated && !chatMessages.length) {
    hydrateConversation(activeConversationId);
  }
}

async function selectConversation(conversationId) {
  if (!conversationId || conversationId === activeConversationId) return;
  activeConversationId = conversationId;
  localStorage.setItem("noteflowConversationId", activeConversationId);
  chatMessages.length = 0;
  conversationHydrated = false;
  renderAgentView();
}

function startNewConversation() {
  activeConversationId = null;
  localStorage.removeItem("noteflowConversationId");
  chatMessages.length = 0;
  conversationHydrated = false;
  renderAgentView();
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
      conversationsList = [created, ...conversationsList.filter((conversation) => conversation.id !== created.id)];
      conversationsLoaded = true;
      refreshConversationList();
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
    loadConversationList();
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
  const agent = message.structuredResponse?.agent || null;
  return `
    <div class="chat-answer-text rich">${content}</div>
    ${citations.length ? `<div class="chat-evidence">${citations.map(renderConversationCitation).join("")}</div>` : ""}
    ${renderAgentSteps(agent)}
  `;
}

function renderAgentSteps(agent) {
  if (!agent?.enabled) return "";
  const trace = Array.isArray(agent.trace) ? agent.trace : [];
  const handles = Array.isArray(agent.handles) ? agent.handles.filter(Boolean) : [];
  if (!trace.length && !handles.length) return "";
  return `
    <details class="agent-steps">
      <summary>
        <span>Agent steps</span>
        <span>${trace.length} step${trace.length === 1 ? "" : "s"}${agent.fallbackUsed ? " · fallback" : ""}</span>
      </summary>
      <div class="agent-step-list">
        ${trace.map(renderAgentStep).join("")}
      </div>
      ${handles.length ? `<div class="agent-handles">${handles.map(renderAgentHandle).join("")}</div>` : ""}
    </details>
  `;
}

function renderAgentStep(step) {
  const tool = step.tool || step.actionType || "step";
  const ok = step.ok === false ? "Failed" : "Done";
  const args = step.args && Object.keys(step.args).length ? `<code>${escapeHtml(JSON.stringify(step.args))}</code>` : "";
  return `
    <article class="agent-step">
      <div class="agent-step-head">
        <strong>${escapeHtml(toolLabel(tool))}</strong>
        <span>${escapeHtml(ok)} · ${Number(step.latencyMs || 0)}ms</span>
      </div>
      ${step.summary ? `<p>${escapeHtml(step.summary)}</p>` : ""}
      ${args ? `<div class="agent-step-args">${args}</div>` : ""}
      ${step.observation ? `<pre>${escapeHtml(compactObservation(step.observation))}</pre>` : ""}
    </article>
  `;
}

function renderAgentHandle(handle) {
  if (!handle?.kind) return "";
  const label = handle.kind === "quiz" ? "Open Quiz" : "Open Flashcards";
  const view = handle.kind === "quiz" ? "quiz" : "flashcards";
  const id = handle.documentId || "";
  return `
    <button type="button" class="secondary agent-handle" data-agent-open="${view}" data-doc-id="${escapeHtml(id)}">
      ${escapeHtml(label)}
    </button>
  `;
}

function toolLabel(tool) {
  return {
    search_notes: "Search notes",
    get_document_section: "Get document section",
    list_documents: "List documents",
    compare_sources: "Compare sources",
    generate_quiz: "Generate quiz",
    create_flashcards: "Create flashcards",
    final_answer: "Final answer",
    fallback: "Fallback",
  }[tool] || tool;
}

function compactObservation(value) {
  const text = String(value || "");
  return text.length <= 700 ? text : `${text.slice(0, 700).trim()}\n...`;
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

async function hydrateConversation(conversationId = activeConversationId) {
  if (!conversationId) return;
  conversationHydrated = true;
  const pendingMessages = [];
  try {
    const stored = await requestJson(`/conversations/${conversationId}/messages`);
    if (conversationId !== activeConversationId) return;
    for (const message of stored) {
      if (message.role === "USER") {
        chatMessages.push({ role: "user", text: message.content_markdown || "" });
      } else if (message.status === "COMPLETED") {
        chatMessages.push({ role: "assistant", html: renderConversationAnswer(message) });
      } else if (message.status === "FAILED") {
        chatMessages.push({ role: "assistant", html: `<p class="chat-note">${escapeHtml(message.error_message || "Answer generation failed")}</p>` });
      } else {
        const index = chatMessages.push({ role: "assistant", html: `<p class="chat-note">Answer generation is still in progress…</p>` }) - 1;
        pendingMessages.push({ messageId: message.id, index });
      }
    }
    refreshChatMessages();
    resumePendingConversationMessages(conversationId, pendingMessages);
  } catch (error) {
    chatMessages.push({
      role: "assistant",
      html: `<p class="chat-note">Could not load this saved conversation. The conversation id is preserved; try again after the API is reachable.\n\n${escapeHtml(formatFetchError(error))}</p>`,
    });
    refreshChatMessages();
  }
}

async function resumePendingConversationMessages(conversationId, pendingMessages) {
  for (const pending of pendingMessages) {
    try {
      const answer = await pollConversationMessage(pending.messageId);
      if (conversationId !== activeConversationId) return;
      chatMessages[pending.index] = { role: "assistant", html: renderConversationAnswer(answer) };
      refreshChatMessages();
      loadConversationList();
    } catch (error) {
      if (conversationId !== activeConversationId) return;
      chatMessages[pending.index] = { role: "assistant", html: `<p class="chat-note">${escapeHtml(formatFetchError(error))}</p>` };
      refreshChatMessages();
    }
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

function agentScopeSummary(item) {
  let scope = {};
  try {
    scope = JSON.parse(item.source_scope_json || "{}");
  } catch {
    scope = {};
  }
  const parts = [];
  const docCount = Array.isArray(scope.documentIds) ? scope.documentIds.length : 0;
  if (docCount > 1) parts.push(`${docCount} documents`);
  if (scope.sectionQuery) parts.push(`Section: ${scope.sectionQuery}`);
  if (Array.isArray(scope.chunkIds) && scope.chunkIds.length) parts.push(`${scope.chunkIds.length} passages`);
  if (scope.focus) parts.push(`Focus: ${scope.focus}`);
  return parts.join(" · ");
}

function renderAgentStudyGroup(items, kind) {
  if (!items?.length) return "";
  const label = kind === "quiz" ? "Agent-designed quizzes" : "Agent-designed decks";
  return `
    <section class="agent-study-group">
      <div class="agent-study-head">
        <span class="agent-badge">✦ AGENT</span>
        <h4>${label}</h4>
        <span class="agent-study-count">${items.length}</span>
      </div>
      ${items.map((item) => renderAgentStudyItem(item, kind)).join("")}
    </section>
  `;
}

function renderAgentStudyItem(item, kind) {
  const summary = agentScopeSummary(item);
  const ready = item.status === "READY";
  const actions = !ready ? "" : kind === "quiz"
    ? `<button class="secondary" data-study-action="start-quiz" data-quiz-id="${item.id}">Start quiz</button>`
    : `<button class="secondary" data-study-action="review" data-deck-id="${item.id}">Review</button>
       <button class="secondary" data-study-action="browse-cards" data-deck-id="${item.id}">Browse</button>`;
  return `
    <article class="agent-study-item">
      <div class="agent-study-item-main">
        <strong>${escapeHtml(item.title || "Agent generation")}</strong>
        ${summary ? `<span class="agent-study-scope">${escapeHtml(summary)}</span>` : ""}
      </div>
      <div class="agent-study-item-side">
        <span class="badge ${statusClass(item.status)}">${escapeHtml(item.status || "")}</span>
        ${actions}
      </div>
    </article>
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
      const allDecks = await readJson(response);
      if (!response.ok) throw new Error(allDecks.message || "Could not load flashcard decks");
      const decks = allDecks.filter((d) => (d.origin || "SECTION") !== "AGENT");
      const agentDecks = allDecks.filter((d) => d.origin === "AGENT");
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
        ${renderAgentStudyGroup(agentDecks, "cards")}
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
      const allQuizzes = await readJson(response);
      if (!response.ok) throw new Error(allQuizzes.message || "Could not load quizzes");
      const quizzes = allQuizzes.filter((q) => (q.origin || "SECTION") !== "AGENT");
      const agentQuizzes = allQuizzes.filter((q) => q.origin === "AGENT");
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
        ${renderAgentStudyGroup(agentQuizzes, "quiz")}
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
// ---------------------------------------------------------------------------
// Settings view
// ---------------------------------------------------------------------------
const GEMINI_LLM_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"];
const OPENAI_LLM_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"];
const GEMINI_EMBEDDING_MODELS = ["gemini-embedding-001"];
const OPENAI_EMBEDDING_MODELS = ["text-embedding-3-small", "text-embedding-3-large"];

function modelOptions(models) {
  return models.map((model) => `<option value="${model}"></option>`).join("");
}

function providerOptions(selected) {
  return ["auto", "gemini", "openai", "disabled"]
    .map((provider) => `<option value="${provider}" ${provider === selected ? "selected" : ""}>${provider}</option>`)
    .join("");
}

async function renderSettingsView() {
  viewRoot.innerHTML = `
    <div class="view-header">
      <div>
        <div class="eyebrow">Settings</div>
        <h1>AI providers & models</h1>
      </div>
    </div>
    <div class="general-grid">
      <section class="panel">
        <div class="eyebrow">Loading</div>
        <p>Loading current settings…</p>
      </section>
    </div>
  `;
  let current;
  try {
    const response = await fetch(`${API_BASE_URL}/settings/ai`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    current = await response.json();
  } catch (error) {
    viewRoot.querySelector(".panel").innerHTML = `
      <div class="eyebrow">Error</div>
      <p>Could not load settings: ${error.message}</p>
    `;
    return;
  }
  const grid = viewRoot.querySelector(".general-grid");
  grid.innerHTML = `
    <section class="panel">
      <div class="eyebrow">API keys</div>
      <h2>Provider credentials</h2>
      <p class="hint">Keys are stored on your own NoteFlow server and never shown back in full.</p>
      <form id="settings-form" class="form">
        <label>Gemini API key
          <input id="settings-gemini-key" type="password" autocomplete="off"
            placeholder="${current.geminiKeySet ? `Saved (${current.geminiKeyHint}) — leave blank to keep` : "Not set"}" />
        </label>
        <label>OpenAI API key
          <input id="settings-openai-key" type="password" autocomplete="off"
            placeholder="${current.openaiKeySet ? `Saved (${current.openaiKeyHint}) — leave blank to keep` : "Not set"}" />
        </label>

        <div class="eyebrow">Chat & notes model</div>
        <label>LLM provider
          <select id="settings-llm-provider">${providerOptions(current.llmProvider)}</select>
        </label>
        <label>Gemini model
          <input id="settings-gemini-llm-model" list="gemini-llm-models"
            value="${current.geminiLlmModel || ""}" placeholder="gemini-2.5-flash (default)" />
          <datalist id="gemini-llm-models">${modelOptions(GEMINI_LLM_MODELS)}</datalist>
        </label>
        <label>OpenAI model
          <input id="settings-openai-llm-model" list="openai-llm-models"
            value="${current.openaiLlmModel || ""}" placeholder="gpt-4o-mini (default)" />
          <datalist id="openai-llm-models">${modelOptions(OPENAI_LLM_MODELS)}</datalist>
        </label>

        <div class="eyebrow">Embeddings (semantic search)</div>
        <label>Embedding provider
          <select id="settings-embedding-provider">${providerOptions(current.embeddingProvider)}</select>
        </label>
        <label>Gemini embedding model
          <input id="settings-gemini-embedding-model" list="gemini-embedding-models"
            value="${current.geminiEmbeddingModel || ""}" placeholder="gemini-embedding-001 (default)" />
          <datalist id="gemini-embedding-models">${modelOptions(GEMINI_EMBEDDING_MODELS)}</datalist>
        </label>
        <label>OpenAI embedding model
          <input id="settings-openai-embedding-model" list="openai-embedding-models"
            value="${current.openaiEmbeddingModel || ""}" placeholder="text-embedding-3-small (default)" />
          <datalist id="openai-embedding-models">${modelOptions(OPENAI_EMBEDDING_MODELS)}</datalist>
        </label>
        <p class="hint">Changing the embedding provider or model only affects new embeddings —
          regenerate document embeddings afterwards so semantic search matches.</p>

        <button type="submit" class="primary">Save settings</button>
        <div id="settings-status" class="status"></div>
      </form>
    </section>
    <section class="panel">
      <div class="eyebrow">Effective configuration</div>
      <h2>What is active right now</h2>
      <div id="settings-effective">${renderEffectiveSettings(current.effective)}</div>
      <p class="hint">"auto" resolves to whichever provider has an API key
        (Gemini first). Environment variables remain the fallback when a field
        is left empty.</p>
    </section>
  `;
  grid.querySelector("#settings-form").addEventListener("submit", saveSettings);
}

function renderEffectiveSettings(effective) {
  if (!effective) return "";
  return `
    <ul class="meta-list">
      <li><strong>LLM provider:</strong> ${effective.llmProvider}</li>
      <li><strong>Embedding provider:</strong> ${effective.embeddingProvider}</li>
      <li><strong>Embedding model:</strong> ${effective.embeddingModel}</li>
    </ul>
  `;
}

async function saveSettings(event) {
  event.preventDefault();
  const status = viewRoot.querySelector("#settings-status");
  status.textContent = "Saving…";
  const geminiKey = viewRoot.querySelector("#settings-gemini-key").value.trim();
  const openaiKey = viewRoot.querySelector("#settings-openai-key").value.trim();
  const payload = {
    // Keys: only send when the user typed one, so an untouched field keeps
    // the saved value instead of clearing it.
    ...(geminiKey ? { geminiApiKey: geminiKey } : {}),
    ...(openaiKey ? { openaiApiKey: openaiKey } : {}),
    llmProvider: viewRoot.querySelector("#settings-llm-provider").value,
    geminiLlmModel: viewRoot.querySelector("#settings-gemini-llm-model").value.trim(),
    openaiLlmModel: viewRoot.querySelector("#settings-openai-llm-model").value.trim(),
    embeddingProvider: viewRoot.querySelector("#settings-embedding-provider").value,
    geminiEmbeddingModel: viewRoot.querySelector("#settings-gemini-embedding-model").value.trim(),
    openaiEmbeddingModel: viewRoot.querySelector("#settings-openai-embedding-model").value.trim(),
  };
  try {
    const response = await fetch(`${API_BASE_URL}/settings/ai`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const saved = await response.json();
    status.textContent = "Saved.";
    const effectiveContainer = viewRoot.querySelector("#settings-effective");
    if (effectiveContainer) effectiveContainer.innerHTML = renderEffectiveSettings(saved.effective);
    viewRoot.querySelector("#settings-gemini-key").value = "";
    viewRoot.querySelector("#settings-openai-key").value = "";
    viewRoot.querySelector("#settings-gemini-key").placeholder =
      saved.geminiKeySet ? `Saved (${saved.geminiKeyHint}) — leave blank to keep` : "Not set";
    viewRoot.querySelector("#settings-openai-key").placeholder =
      saved.openaiKeySet ? `Saved (${saved.openaiKeyHint}) — leave blank to keep` : "Not set";
  } catch (error) {
    status.textContent = `Save failed: ${error.message}`;
  }
}

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
    const [summaryResponse, chunksResponse, assetsResponse, blocksResponse] = await Promise.all([
      fetch(`${API_BASE_URL}/documents/${documentId}/parse-result`),
      fetch(`${API_BASE_URL}/documents/${documentId}/chunks?limit=120`),
      fetch(`${API_BASE_URL}/documents/${documentId}/assets`),
      fetch(`${API_BASE_URL}/documents/${documentId}/layout-blocks?limit=240`),
    ]);
    const summary = await readJson(summaryResponse);
    const chunks = await readJson(chunksResponse);
    const assets = await readJson(assetsResponse);
    const blocks = await readJson(blocksResponse);
    if (!summaryResponse.ok) throw new Error(summary.message || "Parse summary is not available yet");
    if (!chunksResponse.ok) throw new Error(chunks.message || "Chunks are not available yet");
    if (!assetsResponse.ok) throw new Error(assets.message || "Visual assets are not available yet");
    if (!blocksResponse.ok) throw new Error(blocks.message || "Layout blocks are not available yet");
    renderParsedOutput(summary, chunks, assets, blocks, [], [], [], null, true);
    loadParsedOutputDetails(documentId, summary, chunks, assets, blocks);
  } catch (error) {
    output.classList.add("muted");
    output.textContent = error.message;
  }
}

async function loadParsedOutputDetails(documentId, summary, chunks, assets, blocks) {
  const [regionsResult, vlmResult, markdownPagesResult, markdownResult] = await Promise.allSettled([
    requestJson(`/documents/${documentId}/visual-regions?limit=80`),
    requestJson(`/documents/${documentId}/vlm-results?limit=80`),
    requestJson(`/documents/${documentId}/markdown-pages?limit=20`),
    requestJson(`/documents/${documentId}/markdown?previewChars=24000`),
  ]);
  if (!generalOutput()) return;
  renderParsedOutput(
    summary,
    chunks,
    assets,
    blocks,
    fulfilledValue(regionsResult, []),
    fulfilledValue(vlmResult, []),
    fulfilledValue(markdownPagesResult, []),
    fulfilledValue(markdownResult, null),
    false
  );
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

function renderParsedOutput(summary, chunks, assets, blocks, regions, vlmResults, markdownPages, markdownDocument, detailsLoading = false) {
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
    ${detailsLoading ? `<div class="status-card muted">Loading visual details and Markdown…</div>` : ""}
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
let editorDraftTitle = "Untitled note";
// Standalone (folder-backed) note currently open in the editor, or null when a
// document note is open. Note tabs use the key `note:<id>`; document tabs use
// the raw document id.
let editorNoteId = null;
let editorNoteTitles = parseJsonSafe(localStorage.getItem("noteflowEditorNoteTitles")) || {};
// Library (Files section) state, backed by /folders and /notes.
let libraryFolders = [];
let libraryNotes = [];
let librarySelectedFolderId = localStorage.getItem("noteflowLibraryFolder") || "ALL";
let libraryExpanded = new Set(parseJsonSafe(localStorage.getItem("noteflowLibraryExpanded")) || []);
let libraryDragNoteId = null;
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

function closeEditorTab(key) {
  const index = editorTabs.indexOf(key);
  if (index === -1) return;
  editorTabs.splice(index, 1);
  const wasActive = isNoteTabKey(key) ? editorNoteId === noteIdFromKey(key) : (!editorNoteId && activeDocumentId === key);
  if (wasActive) {
    editorNoteId = null;
    activeDocumentId = null;
    localStorage.setItem("noteflowActiveDocument", "");
    const next = editorTabs[index] || editorTabs[index - 1] || null;
    if (next) {
      persistEditorTabs();
      activateEditorTab(next);
      return;
    }
  }
  if (!editorTabs.length) {
    editorHomeMode = true;
    localStorage.setItem("noteflowEditorHome", "1");
  }
  persistEditorTabs();
  navigate("editor");
}

function activateEditorTab(key) {
  editorHomeMode = false;
  localStorage.setItem("noteflowEditorHome", "0");
  if (isNoteTabKey(key)) {
    editorNoteId = noteIdFromKey(key);
    activeDocumentId = null;
    localStorage.setItem("noteflowActiveDocument", "");
  } else {
    editorNoteId = null;
    activeDocumentId = key;
    localStorage.setItem("noteflowActiveDocument", key);
  }
  navigate("editor");
}

function openStandaloneNoteInEditor(noteId, title) {
  const key = `note:${noteId}`;
  if (title) {
    editorNoteTitles[noteId] = title;
    localStorage.setItem("noteflowEditorNoteTitles", JSON.stringify(editorNoteTitles));
  }
  if (!editorTabs.includes(key)) {
    editorTabs.push(key);
    persistEditorTabs();
  }
  activateEditorTab(key);
}

function isNoteTabKey(key) {
  return typeof key === "string" && key.startsWith("note:");
}

function noteIdFromKey(key) {
  return key.slice(5);
}

function editorTabLabel(key) {
  if (isNoteTabKey(key)) {
    const id = noteIdFromKey(key);
    return editorNoteTitles[id] || libraryNotes.find((n) => n.id === id)?.title || "Untitled note";
  }
  return documentsMap.get(key)?.title || "Document";
}

function isEditorTabActive(key) {
  if (editorHomeMode) return false;
  if (isNoteTabKey(key)) return editorNoteId === noteIdFromKey(key);
  return !editorNoteId && key === activeDocumentId;
}

function renderEditorTabs() {
  return `
    <div class="editor-tabs">
      ${editorTabs.map((key) => {
        const label = editorTabLabel(key);
        return `
          <div class="editor-tab ${isEditorTabActive(key) ? "active" : ""}" data-editor-tab="${escapeHtml(key)}" title="${escapeHtml(label)}">
            <span class="editor-tab-title">${escapeHtml(label)}</span>
            <button type="button" class="editor-tab-close" data-editor-tab-close="${escapeHtml(key)}" title="Close tab">✕</button>
          </div>
        `;
      }).join("")}
      <div class="editor-tab-add">
        <button type="button" class="editor-tab-plus" data-editor-tab-create title="New / open">＋</button>
      </div>
    </div>
  `;
}


// ---------------------------------------------------------------------------
// Files section: backend-persisted folders (nested tree) + notes.
// ---------------------------------------------------------------------------
async function renderFoldersView() {
  viewRoot.innerHTML = `
    <div class="view-header">
      <div><div class="eyebrow">Files</div><h1>Library</h1></div>
      <div class="editor-actions">
        <button type="button" data-library-action="new-note">New note</button>
      </div>
    </div>
    <div class="library-layout">
      <aside class="library-tree" id="library-tree"><div class="study-loading">Loading…</div></aside>
      <section class="library-notes" id="library-notes"></section>
    </div>
    <input type="file" id="library-import-input" accept=".md,.markdown,.txt,text/markdown,text/plain" multiple hidden />
  `;
  wireLibraryEvents();
  await refreshLibrary();
}

// Selection model for the left panel:
//   "ALL"            – every note
//   "KIND:AI_NOTE"   – smart view filtered by source kind
//   "KIND:RAW" / "KIND:IMPORT" / "KIND:BLANK"
//   "UNFILED"        – notes with no folder
//   <uuid>           – a real folder
const LIBRARY_KIND_VIEWS = [
  ["KIND:AI_NOTE", "AI notes", "AI_NOTE"],
  ["KIND:RAW", "PDF markdown", "RAW"],
  ["KIND:IMPORT", "Imported", "IMPORT"],
  ["KIND:BLANK", "My notes", "BLANK"],
];

async function refreshLibrary() {
  try {
    const [foldersRes, notesRes] = await Promise.all([
      fetch(`${API_BASE_URL}/folders`),
      fetch(`${API_BASE_URL}/notes`),
    ]);
    libraryFolders = foldersRes.ok ? await readJson(foldersRes) : [];
    libraryNotes = notesRes.ok ? await readJson(notesRes) : [];
    // Fall back to "All notes" if the remembered folder no longer exists.
    if (isRealLibraryFolder() && !libraryFolders.some((folder) => folder.id === librarySelectedFolderId)) {
      librarySelectedFolderId = "ALL";
      localStorage.setItem("noteflowLibraryFolder", "ALL");
    }
  } catch (error) {
    const tree = viewRoot.querySelector("#library-tree");
    if (tree) tree.innerHTML = `<div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>`;
    return;
  }
  renderLibraryTree();
  renderLibraryNotes();
}

function libraryChildFolders(parentId) {
  return libraryFolders
    .filter((folder) => (folder.parentId || null) === (parentId || null))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function libraryNoteCount(folderId) {
  return libraryNotes.filter((note) => (note.folderId || null) === (folderId || null)).length;
}

function libraryKindCount(kind) {
  return libraryNotes.filter((note) => (note.sourceKind || "BLANK") === kind).length;
}

function renderLibraryTree() {
  const tree = viewRoot.querySelector("#library-tree");
  if (!tree) return;
  const renderNode = (folder, depth) => {
    const children = libraryChildFolders(folder.id);
    const expanded = libraryExpanded.has(folder.id);
    const twisty = children.length ? (expanded ? "▾" : "▸") : "·";
    return `
      <div class="library-folder-row ${librarySelectedFolderId === folder.id ? "active" : ""}"
           data-library-folder="${escapeHtml(folder.id)}" data-drop-folder="${escapeHtml(folder.id)}"
           style="padding-left:${depth * 14 + 6}px" title="${escapeHtml(folder.name)}">
        <button type="button" class="library-twisty" data-library-toggle="${escapeHtml(folder.id)}" ${children.length ? "" : "disabled"}>${twisty}</button>
        <span class="folder-icon">▣</span>
        <span class="library-folder-name">${escapeHtml(folder.name)}</span>
        <button type="button" class="library-subfolder" data-library-subfolder="${escapeHtml(folder.id)}" title="New subfolder">＋</button>
        <span class="library-folder-count">${libraryNoteCount(folder.id) || ""}</span>
      </div>
      ${expanded ? children.map((child) => renderNode(child, depth + 1)).join("") : ""}
    `;
  };
  const smartView = (id, label, icon, count) => `
    <div class="library-folder-row smart ${librarySelectedFolderId === id ? "active" : ""}" data-library-folder="${id}">
      <span class="library-twisty disabled">·</span>
      <span class="folder-icon">${icon}</span>
      <span class="library-folder-name">${label}</span>
      <span class="library-folder-count">${count || ""}</span>
    </div>
  `;
  const unfiled = `
    <div class="library-folder-row ${librarySelectedFolderId === "UNFILED" ? "active" : ""}"
         data-library-folder="UNFILED" data-drop-folder="" title="Notes not in any folder">
      <span class="library-twisty disabled">·</span>
      <span class="folder-icon">○</span>
      <span class="library-folder-name">Unfiled</span>
      <span class="library-folder-count">${libraryNoteCount(null) || ""}</span>
    </div>
  `;
  tree.innerHTML = `
    <div class="library-group-label">Views</div>
    ${smartView("ALL", "All notes", "≡", libraryNotes.length)}
    ${LIBRARY_KIND_VIEWS.map(([id, label, kind]) => smartView(id, label, "◆", libraryKindCount(kind))).join("")}
    <div class="library-group-label library-folders-head">
      <span>Folders</span>
      <button type="button" class="library-subfolder" data-library-subfolder="ROOT" title="New folder">＋</button>
    </div>
    ${unfiled}
    ${libraryChildFolders(null).map((folder) => renderNode(folder, 0)).join("") || `<div class="side-empty">No folders yet. Use ＋ to create one.</div>`}
  `;
}

function renderLibraryNotes() {
  const panel = viewRoot.querySelector("#library-notes");
  if (!panel) return;
  const sel = librarySelectedFolderId;
  let notes;
  let heading;
  let eyebrow = "Folder";
  let isRealFolder = false;
  if (sel === "ALL") {
    notes = libraryNotes; heading = "All notes"; eyebrow = "View";
  } else if (sel === "UNFILED") {
    notes = libraryNotes.filter((note) => !note.folderId); heading = "Unfiled";
  } else if (sel.startsWith("KIND:")) {
    const kind = sel.slice(5);
    notes = libraryNotes.filter((note) => (note.sourceKind || "BLANK") === kind);
    heading = LIBRARY_KIND_VIEWS.find(([id]) => id === sel)?.[1] || "Notes";
    eyebrow = "View";
  } else {
    notes = libraryNotes.filter((note) => note.folderId === sel);
    heading = libraryFolders.find((folder) => folder.id === sel)?.name || "Folder";
    isRealFolder = true;
  }
  const kindBadge = (note) => ({ AI_NOTE: "AI Note", RAW: "PDF Markdown", IMPORT: "Imported", BLANK: "Note" }[note.sourceKind] || "Note");
  panel.innerHTML = `
    <div class="folder-content-head">
      <div><div class="eyebrow">${eyebrow}</div><h2>${escapeHtml(heading)}</h2></div>
      <div class="editor-actions">
        <button type="button" class="ghost-button" data-library-action="new-folder" title="${isRealFolder ? `New subfolder in “${escapeHtml(heading)}”` : "New folder at top level"}">New folder</button>
        <button type="button" class="ghost-button" data-library-action="import" title="${isRealFolder ? `Import into “${escapeHtml(heading)}”` : "Import into Unfiled"}">Import .md</button>
        ${isRealFolder ? `
          <button type="button" class="ghost-button" data-library-action="rename-folder">Rename</button>
          <button type="button" class="ghost-button" data-library-action="delete-folder">Delete</button>
        ` : ""}
      </div>
    </div>
    <p class="library-hint">Drag a note onto a folder on the left to move it.</p>
    <div class="library-note-list">
      ${notes.map((note) => `
        <article class="library-note-card" draggable="true" data-library-note="${escapeHtml(note.id)}">
          <span class="library-drag-handle" title="Drag to a folder">⠿</span>
          <button type="button" class="library-note-open" data-library-open="${escapeHtml(note.id)}">
            <strong>${escapeHtml(note.title || "Untitled note")}</strong>
            <span class="library-note-meta">${kindBadge(note)}${note.folderId && sel !== note.folderId ? ` · ${escapeHtml(libraryFolders.find((f) => f.id === note.folderId)?.name || "")}` : ""} · ${new Date(note.updatedAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</span>
          </button>
          <div class="library-note-actions">
            <button type="button" class="icon-button" data-library-export="${escapeHtml(note.id)}" title="Export .md">↓</button>
            <button type="button" class="icon-button" data-library-delete="${escapeHtml(note.id)}" title="Delete note">✕</button>
          </div>
        </article>
      `).join("") || `<div class="side-empty">No notes here yet.</div>`}
    </div>
  `;
}

// Parent folder id used by New folder / Import / New note when the current
// selection is a real folder; null (top level / Unfiled) otherwise.
function currentLibraryFolderTarget() {
  return isRealLibraryFolder() ? librarySelectedFolderId : null;
}

function isRealLibraryFolder() {
  return librarySelectedFolderId !== "ALL"
    && librarySelectedFolderId !== "UNFILED"
    && !librarySelectedFolderId.startsWith("KIND:");
}

function selectLibraryFolder(id) {
  librarySelectedFolderId = id;
  localStorage.setItem("noteflowLibraryFolder", id);
}

// Expands every ancestor of a folder so it is visible in the tree.
function expandLibraryAncestors(folderId) {
  let current = libraryFolders.find((folder) => folder.id === folderId);
  while (current && current.parentId) {
    libraryExpanded.add(current.parentId);
    current = libraryFolders.find((folder) => folder.id === current.parentId);
  }
  localStorage.setItem("noteflowLibraryExpanded", JSON.stringify(Array.from(libraryExpanded)));
}

async function moveLibraryNote(noteId, folderId) {
  try {
    await fetch(`${API_BASE_URL}/notes/${noteId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folderId: folderId || null, move: true }),
    });
    // Switch the right panel into the destination folder (or Unfiled).
    if (folderId) {
      expandLibraryAncestors(folderId);
      selectLibraryFolder(folderId);
    } else {
      selectLibraryFolder("UNFILED");
    }
    await refreshLibrary();
  } catch (error) {
    console.error("Move failed:", error);
  }
}

async function createLibraryFolder(parentId) {
  const name = prompt(parentId ? "New subfolder name" : "New folder name", "New folder");
  if (name === null || !name.trim()) return;
  const folder = await (await fetch(`${API_BASE_URL}/folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim(), parentId: parentId || null }),
  })).json();
  if (parentId) {
    libraryExpanded.add(parentId);
    localStorage.setItem("noteflowLibraryExpanded", JSON.stringify(Array.from(libraryExpanded)));
  }
  if (folder && folder.id) selectLibraryFolder(folder.id);
  await refreshLibrary();
}

function wireLibraryEvents() {
  const importInput = viewRoot.querySelector("#library-import-input");
  importInput.addEventListener("change", async () => {
    const targetFolder = currentLibraryFolderTarget();
    for (const file of Array.from(importInput.files || [])) {
      const form = new FormData();
      form.append("file", file);
      if (targetFolder) form.append("folderId", targetFolder);
      try {
        await fetch(`${API_BASE_URL}/notes/import`, { method: "POST", body: form });
      } catch (error) {
        console.error("Import failed:", error);
      }
    }
    importInput.value = "";
    await refreshLibrary();
  });

  viewRoot.addEventListener("click", async (event) => {
    const subfolder = event.target.closest("[data-library-subfolder]");
    if (subfolder) {
      const parent = subfolder.dataset.librarySubfolder;
      await createLibraryFolder(parent === "ROOT" ? null : parent);
      return;
    }
    const action = event.target.closest("[data-library-action]");
    if (action) {
      await handleLibraryAction(action.dataset.libraryAction);
      return;
    }
    const toggle = event.target.closest("[data-library-toggle]");
    if (toggle) {
      const id = toggle.dataset.libraryToggle;
      if (libraryExpanded.has(id)) libraryExpanded.delete(id);
      else libraryExpanded.add(id);
      localStorage.setItem("noteflowLibraryExpanded", JSON.stringify(Array.from(libraryExpanded)));
      renderLibraryTree();
      return;
    }
    const folderRow = event.target.closest("[data-library-folder]");
    if (folderRow) {
      selectLibraryFolder(folderRow.dataset.libraryFolder);
      renderLibraryTree();
      renderLibraryNotes();
      return;
    }
    const open = event.target.closest("[data-library-open]");
    if (open) {
      openStandaloneNoteInEditor(open.dataset.libraryOpen);
      return;
    }
    const exportBtn = event.target.closest("[data-library-export]");
    if (exportBtn) {
      window.location.href = `${API_BASE_URL}/notes/${exportBtn.dataset.libraryExport}/export`;
      return;
    }
    const del = event.target.closest("[data-library-delete]");
    if (del) {
      if (!confirm("Delete this note? This cannot be undone.")) return;
      await fetch(`${API_BASE_URL}/notes/${del.dataset.libraryDelete}`, { method: "DELETE" });
      await refreshLibrary();
      return;
    }
  });

  // Drag a note card onto a folder (or Unfiled) to move it — file-explorer style.
  viewRoot.addEventListener("dragstart", (event) => {
    const card = event.target.closest("[data-library-note]");
    if (!card) return;
    libraryDragNoteId = card.dataset.libraryNote;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", libraryDragNoteId);
    card.classList.add("dragging");
  });
  viewRoot.addEventListener("dragend", (event) => {
    const card = event.target.closest("[data-library-note]");
    if (card) card.classList.remove("dragging");
    libraryDragNoteId = null;
    viewRoot.querySelectorAll(".drop-target").forEach((el) => el.classList.remove("drop-target"));
  });
  viewRoot.addEventListener("dragover", (event) => {
    const target = event.target.closest("[data-drop-folder]");
    if (!target || !libraryDragNoteId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    viewRoot.querySelectorAll(".drop-target").forEach((el) => el.classList.remove("drop-target"));
    target.classList.add("drop-target");
  });
  viewRoot.addEventListener("dragleave", (event) => {
    const target = event.target.closest("[data-drop-folder]");
    if (target && !target.contains(event.relatedTarget)) target.classList.remove("drop-target");
  });
  viewRoot.addEventListener("drop", async (event) => {
    const target = event.target.closest("[data-drop-folder]");
    if (!target) return;
    event.preventDefault();
    const noteId = libraryDragNoteId || event.dataTransfer.getData("text/plain");
    target.classList.remove("drop-target");
    libraryDragNoteId = null;
    if (noteId) await moveLibraryNote(noteId, target.dataset.dropFolder || null);
  });
}

async function handleLibraryAction(kind) {
  try {
    if (kind === "new-folder") {
      await createLibraryFolder(currentLibraryFolderTarget());
    } else if (kind === "rename-folder" && isRealLibraryFolder()) {
      const current = libraryFolders.find((folder) => folder.id === librarySelectedFolderId);
      const name = prompt("Rename folder", current?.name || "");
      if (name === null || !name.trim()) return;
      await fetch(`${API_BASE_URL}/folders/${librarySelectedFolderId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), move: false }),
      });
      await refreshLibrary();
    } else if (kind === "delete-folder" && isRealLibraryFolder()) {
      if (!confirm("Delete this folder and its subfolders? Notes inside are moved to Unfiled.")) return;
      await fetch(`${API_BASE_URL}/folders/${librarySelectedFolderId}`, { method: "DELETE" });
      selectLibraryFolder("ALL");
      await refreshLibrary();
    } else if (kind === "new-note") {
      const folderId = currentLibraryFolderTarget();
      const response = await fetch(`${API_BASE_URL}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "Untitled note", markdown: "", folderId, sourceKind: "BLANK" }),
      });
      const note = await readJson(response);
      await refreshLibrary();
      openStandaloneNoteInEditor(note.id);
    } else if (kind === "import") {
      viewRoot.querySelector("#library-import-input")?.click();
    }
  } catch (error) {
    console.error("Library action failed:", error);
  }
}

async function renderEditorView() {
  const documents = Array.from(documentsMap.values());
  // Reconcile tabs: keep note tabs (backed by the library), drop document tabs
  // whose document no longer exists.
  editorTabs = editorTabs.filter((key) => isNoteTabKey(key) ? true : documentsMap.has(key));
  if (editorStartDocumentId && !documentsMap.has(editorStartDocumentId)) editorStartDocumentId = null;
  if (activeDocumentId && documentsMap.has(activeDocumentId) && !editorTabs.includes(activeDocumentId)) {
    editorTabs.push(activeDocumentId);
  }
  // Resolve the active tab. A note tab wins when editorNoteId points at one.
  if (editorNoteId && !editorTabs.includes(`note:${editorNoteId}`)) editorNoteId = null;
  if (!editorNoteId && !activeDocumentId && editorTabs.length) {
    const first = editorTabs[0];
    if (isNoteTabKey(first)) editorNoteId = noteIdFromKey(first);
    else activeDocumentId = first;
  }
  if (!editorStartDocumentId && documents.length) editorStartDocumentId = documents[0].id;
  localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId || "");
  persistEditorTabs();
  const doc = editorNoteId ? null : activeDocument();
  const startDoc = doc || (editorStartDocumentId ? documentsMap.get(editorStartDocumentId) : null) || null;
  const hasActive = Boolean(editorNoteId || doc);
  const showHome = editorHomeMode || !editorTabs.length || !hasActive;
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
      ${renderEditorTabs()}
      <section class="editor-home-page">
        <div class="editor-home-inner">
          ${renderEditorStartContent()}
        </div>
      </section>
      ${renderEditorSourceModal()}
    `;
    wireEditorHomeEvents();
    return;
  }
  const headerTitle = editorNoteId ? (editorNoteTitles[editorNoteId] || "Note") : (doc || startDoc)?.title;
  viewRoot.innerHTML = `
    <div class="view-header editor-header">
      <div>
        <div class="eyebrow">Editor${headerTitle ? ` · ${escapeHtml(headerTitle)}` : ""}</div>
        <h1>My Notes</h1>
      </div>
      <div class="editor-actions">
        <span id="editor-save-status" class="editor-save-status"></span>
        ${editorNoteId ? `<button type="button" class="ghost-button" data-editor-action="rename">Rename</button>` : ""}
        <button type="button" class="ghost-button" data-editor-action="reinit" ${!startDoc ? "disabled" : ""}>Start over…</button>
        <button type="button" data-editor-action="export">Export .md</button>
      </div>
    </div>
    ${renderEditorTabs()}
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
  wireEditorEvents(doc, startDoc);
  if (editorNoteId) {
    await loadStandaloneNote(editorNoteId);
  } else {
    await loadEditorNote(doc);
  }
}

function renderEditorStartContent() {
  return `
    <div class="editor-start">
      <h2>New note</h2>
      <p class="editor-start-sub">Blank notes are saved to your Files library. You can also seed a note from a document.</p>
      <div class="editor-start-options">
        <button type="button" class="editor-start-card" data-editor-blank>
          <span class="start-card-title">Blank note</span>
          <span class="start-card-sub">Create a note in your library and start writing.</span>
        </button>
        <button type="button" class="editor-start-card" data-editor-source-picker="AI_NOTE">
          <span class="start-card-title">From AI Note</span>
          <span class="start-card-sub">Choose a READY AI note to copy into a document note.</span>
        </button>
        <button type="button" class="editor-start-card" data-editor-source-picker="RAW">
          <span class="start-card-title">From PDF Markdown</span>
          <span class="start-card-sub">Choose a parsed PDF Markdown file to copy.</span>
        </button>
      </div>
      <div id="editor-start-error"></div>
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
}

// Blank note: create a backend note (Unfiled) and open it as a note tab, so it
// is persisted and appears in the Files library immediately.
async function startBlankEditorDraft() {
  try {
    const response = await fetch(`${API_BASE_URL}/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "Untitled note", markdown: "", folderId: null, sourceKind: "BLANK" }),
    });
    const note = await readJson(response);
    if (!response.ok) throw new Error(note.message || "Could not create note");
    openStandaloneNoteInEditor(note.id, note.title);
  } catch (error) {
    const errorBox = viewRoot.querySelector("#editor-start-error");
    if (errorBox) errorBox.innerHTML = `<div class="status-card study-error">${escapeHtml(formatFetchError(error))}</div>`;
  }
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
  viewRoot.querySelector(".editor-header").addEventListener("click", async (event) => {
    const action = event.target.closest("[data-editor-action]");
    if (!action) return;
    if (action.dataset.editorAction === "rename" && editorNoteId) {
      const current = editorNoteTitles[editorNoteId] || editorNoteTitle || "Untitled note";
      const name = prompt("Rename note", current);
      if (name === null || !name.trim()) return;
      editorNoteTitle = name.trim();
      editorNoteTitles[editorNoteId] = editorNoteTitle;
      localStorage.setItem("noteflowEditorNoteTitles", JSON.stringify(editorNoteTitles));
      try {
        await fetch(`${API_BASE_URL}/notes/${editorNoteId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: editorNoteTitle }),
        });
      } catch (error) {
        console.error("Rename failed:", error);
      }
      navigate("editor");
    }
    if (action.dataset.editorAction === "export") exportEditorMarkdown(activeDocument() || startDoc);
    if (action.dataset.editorAction === "reinit") {
      editorHomeMode = true;
      editorStartDocumentId = startDoc?.id || activeDocumentId || editorStartDocumentId;
      localStorage.setItem("noteflowEditorHome", "1");
      localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId || "");
      navigate("editor");
    }
  });
}

async function loadEditorNote(doc) {
  const shell = viewRoot.querySelector("#editor-note-shell");
  if (!shell) return;
  if (!doc) return;
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
    await loadEditorMarkdownSections(doc, payload.markdown || "");
    setEditorStatus("Saved");
  } catch (error) {
    if (error instanceof TypeError) {
      // API unreachable: degrade to browser-local persistence.
      editorOfflineMode = true;
      const local = parseJsonSafe(localStorage.getItem(editorLocalKey(doc.id)));
      editorNoteTitle = local?.title || `${doc.title} - My Notes`;
      editorDraftTitle = editorNoteTitle;
      await loadEditorMarkdownSections(doc, local?.markdown || "");
      setEditorStatus("Offline · stored in this browser", true);
      return;
    }
    shell.innerHTML = `<div class="status-card study-error">${escapeHtml(error.message || "Could not load the note")}</div>`;
  }
}

// Loads a standalone (folder-backed) note into the editor.
async function loadStandaloneNote(noteId) {
  const shell = viewRoot.querySelector("#editor-note-shell");
  if (!shell) return;
  editorOfflineMode = false;
  try {
    const response = await fetch(`${API_BASE_URL}/notes/${noteId}`);
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.message || "Could not load the note");
    editorNoteTitle = payload.title || "Untitled note";
    editorDraftTitle = editorNoteTitle;
    editorNoteTitles[noteId] = editorNoteTitle;
    localStorage.setItem("noteflowEditorNoteTitles", JSON.stringify(editorNoteTitles));
    await loadEditorMarkdownSections({ id: null, title: editorNoteTitle }, payload.markdown || "");
    setEditorStatus("Saved");
  } catch (error) {
    shell.innerHTML = `<div class="status-card study-error">${escapeHtml(error.message || formatFetchError(error))}</div>`;
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
    editorNoteId = null;
    editorStartDocumentId = doc.id;
    editorHomeMode = false;
    localStorage.setItem("noteflowActiveDocument", activeDocumentId);
    localStorage.setItem("noteflowEditorStartDocument", editorStartDocumentId);
    localStorage.setItem("noteflowEditorHome", "0");
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
  const noteId = editorNoteId;
  applyEditorSectionMarkdown(markdown);
  const fullMarkdown = editorFullMarkdown;
  editorDirty = false;
  rebuildEditorOutline();
  updateEditorSentinel();
  // Standalone library note.
  if (noteId) {
    try {
      const response = await fetch(`${API_BASE_URL}/notes/${noteId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: editorNoteTitle, markdown: fullMarkdown }),
      });
      if (!response.ok) throw new Error("Save failed");
      setEditorStatus(`Saved · ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
    } catch {
      setEditorStatus("Could not save (offline)", true);
    }
    return;
  }
  if (!documentId) {
    setEditorStatus("Unsaved draft", true);
    return;
  }
  // Per-document editable note.
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
  const conversationNew = event.target.closest("[data-conversation-new]");
  if (conversationNew) {
    startNewConversation();
    return;
  }
  const conversationRow = event.target.closest("[data-conversation-id]");
  if (conversationRow) {
    await selectConversation(conversationRow.dataset.conversationId);
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
    editorHomeMode = true;
    localStorage.setItem("noteflowEditorHome", "1");
    navigate("editor");
    return;
  }
  const editorTab = event.target.closest("[data-editor-tab]");
  if (editorTab && !event.target.closest("[data-editor-tab-close]")) {
    activateEditorTab(editorTab.dataset.editorTab);
    return;
  }
  const refreshView = event.target.closest("[data-refresh-view]");
  if (refreshView) {
    navigate(refreshView.dataset.refreshView);
    return;
  }
  const agentOpen = event.target.closest("[data-agent-open]");
  if (agentOpen) {
    if (agentOpen.dataset.docId) selectDocument(agentOpen.dataset.docId);
    navigate(agentOpen.dataset.agentOpen);
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

function formatConversationTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
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

function fulfilledValue(result, fallback) {
  return result.status === "fulfilled" ? result.value : fallback;
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
