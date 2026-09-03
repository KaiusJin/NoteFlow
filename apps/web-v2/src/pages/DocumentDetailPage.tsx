import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { EmptyState } from "../components/EmptyState";
import { StatusPill } from "../components/StatusPill";
import { ApiError, apiRequest } from "../lib/api";
import { deleteDraft, loadDraft, saveDraft } from "../lib/drafts";
import type { AiNote, DocumentSummary, EditableNote, TaskSummary } from "../types";

type NoteSource = "BLANK" | "RAW" | "AI_NOTE";

export default function DocumentDetailPage() {
  const { documentId } = useParams();
  const auth = useAuth();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [draftReady, setDraftReady] = useState(false);
  const [preview, setPreview] = useState(false);
  const resolvedDocumentId = documentId ?? "";
  const draftKey = `${auth.user?.id ?? "anonymous"}:document:${resolvedDocumentId || "missing"}`;

  const document = useQuery({
    queryKey: ["document", resolvedDocumentId],
    queryFn: () => apiRequest<DocumentSummary>(`/documents/${resolvedDocumentId}`),
    enabled: Boolean(resolvedDocumentId),
    refetchInterval: (query) => query.state.data?.status === "PARSING" || query.state.data?.aiNoteStatus === "GENERATING" || query.state.data?.embeddingStatus === "PROCESSING" ? 2_000 : false
  });
  const tasks = useQuery({
    queryKey: ["document", resolvedDocumentId, "tasks"],
    queryFn: () => apiRequest<TaskSummary[]>(`/documents/${resolvedDocumentId}/tasks`),
    enabled: Boolean(resolvedDocumentId),
    refetchInterval: (query) => query.state.data?.some((task) => task.status === "PENDING" || task.status === "PROCESSING") ? 2_000 : false
  });
  const aiNote = useQuery({
    queryKey: ["document", resolvedDocumentId, "ai-note"],
    queryFn: () => optionalRequest<AiNote>(`/documents/${resolvedDocumentId}/notes`),
    enabled: Boolean(resolvedDocumentId && document.data && document.data.aiNoteStatus !== "NOT_STARTED"),
    refetchInterval: (query) => query.state.data?.status === "GENERATING" ? 2_000 : false
  });
  const editable = useQuery({
    queryKey: ["document", resolvedDocumentId, "editable-note"],
    queryFn: () => optionalRequest<EditableNote>(`/documents/${resolvedDocumentId}/editable-note`),
    enabled: Boolean(resolvedDocumentId)
  });

  useEffect(() => {
    if (editable.isLoading || draftReady) return;
    let cancelled = false;
    void loadDraft(draftKey).then((draft) => {
      if (cancelled) return;
      const server = editable.data;
      const draftIsNewer = draft && (!server || new Date(draft.updatedAt) > new Date(server.updatedAt));
      setTitle(server?.title ?? `${document.data?.title ?? "Document"} - My Notes`);
      setMarkdown(draftIsNewer ? draft.markdown : (server?.markdown ?? ""));
      setDraftReady(true);
    });
    return () => { cancelled = true; };
  }, [document.data?.title, draftKey, draftReady, editable.data, editable.isLoading]);

  useEffect(() => {
    if (!draftReady) return;
    const timer = window.setTimeout(() => {
      void saveDraft({ key: draftKey, markdown, serverVersion: editable.data?.updatedAt ?? null, updatedAt: new Date().toISOString() });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [draftKey, draftReady, editable.data?.updatedAt, markdown]);

  useEffect(() => {
    if (document.data?.aiNoteStatus === "READY") void queryClient.invalidateQueries({ queryKey: ["document", resolvedDocumentId, "ai-note"] });
  }, [document.data?.aiNoteStatus, queryClient, resolvedDocumentId]);

  const generate = useMutation({
    mutationFn: () => apiRequest(`/documents/${resolvedDocumentId}/notes`, { method: "POST" }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["document", resolvedDocumentId] }),
        queryClient.invalidateQueries({ queryKey: ["document", resolvedDocumentId, "tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] })
      ]);
    }
  });
  const initialize = useMutation({
    mutationFn: (source: NoteSource) => apiRequest<EditableNote>(`/documents/${resolvedDocumentId}/editable-note`, { method: "POST", body: JSON.stringify({ source }) }),
    onSuccess: async (note) => {
      setTitle(note.title);
      setMarkdown(note.markdown);
      setDraftReady(true);
      await deleteDraft(draftKey);
      queryClient.setQueryData(["document", resolvedDocumentId, "editable-note"], note);
    }
  });
  const save = useMutation({
    mutationFn: () => apiRequest<EditableNote>(`/documents/${resolvedDocumentId}/editable-note`, { method: "PUT", body: JSON.stringify({ title: title.trim(), markdown }) }),
    onSuccess: async (note) => {
      queryClient.setQueryData(["document", resolvedDocumentId, "editable-note"], note);
      await deleteDraft(draftKey);
    }
  });

  if (!resolvedDocumentId) return <Navigate to="/documents" replace />;
  if (document.isLoading) return <div className="page-loader" role="status">Opening document…</div>;
  if (document.isError || !document.data) return <div className="page"><Link className="back-link" to="/documents">← Documents</Link><EmptyState icon="!" title="Document unavailable">{document.error?.message ?? "This document could not be loaded."}</EmptyState></div>;
  const source = document.data;
  const activeTasks = (tasks.data ?? []).filter((task) => task.status === "PENDING" || task.status === "PROCESSING");

  return (
    <div className="page document-detail-page">
      <Link className="back-link" to="/documents">← Back to documents</Link>
      <header className="document-hero">
        <div><p className="eyebrow">Document workspace</p><h1>{source.title}</h1><p>{source.originalFilename} · {source.pageCount ?? "—"} pages · {formatBytes(source.fileSize)}</p></div>
        <StatusPill status={source.status} />
      </header>

      <section className="document-status-grid" aria-label="Document processing status">
        <StatusCard label="Parsing" status={source.status} detail={source.status === "READY" ? "Source is searchable" : "Extracting structure and pages"} />
        <StatusCard label="Embeddings" status={source.embeddingStatus ?? "NOT_STARTED"} detail="Semantic retrieval index" />
        <StatusCard label="AI notes" status={source.aiNoteStatus ?? "NOT_STARTED"} detail="Grounded study summary" />
      </section>

      {activeTasks.length ? <section className="panel active-work"><p className="eyebrow">Background work</p>{activeTasks.map((task) => <div className="task-row" key={task.id}><div><strong>{task.taskType.replaceAll("_", " ")}</strong><small>{task.currentStep.replaceAll("_", " ")}</small></div><span>{task.progress}%</span><div className="progress-track"><i style={{ width: `${Math.max(4, task.progress)}%` }} /></div></div>)}</section> : null}

      <div className="document-workspace-grid">
        <section className="panel ai-note-panel">
          <div className="section-heading"><div><p className="eyebrow">Grounded synthesis</p><h2>AI notes</h2></div>{source.status === "READY" ? <button className="secondary-button" type="button" disabled={generate.isPending || source.aiNoteStatus === "GENERATING"} onClick={() => generate.mutate()}>{source.aiNoteStatus === "READY" ? "Regenerate" : source.aiNoteStatus === "GENERATING" ? "Generating…" : "Generate notes"}</button> : null}</div>
          {generate.isError ? <p className="inline-error">{generate.error.message}</p> : null}
          {aiNote.data?.status === "READY" ? <article className="markdown-document"><h3>{aiNote.data.title}</h3>{aiNote.data.summary ? <p className="note-summary">{aiNote.data.summary}</p> : null}<pre>{aiNote.data.markdown}</pre></article> : <EmptyState icon="✦" title={source.status === "READY" ? "Generate a grounded note" : "Parsing comes first"}>{source.status === "READY" ? "The worker will create a cited summary from this document." : "AI notes become available once the source is ready."}</EmptyState>}
        </section>

        <section className="panel editor-panel">
          <div className="section-heading"><div><p className="eyebrow">Offline-safe workspace</p><h2>My notes</h2></div><div className="editor-actions"><button className="text-button" type="button" onClick={() => setPreview((value) => !value)}>{preview ? "Edit" : "Preview"}</button><button className="primary-button" type="button" disabled={!draftReady || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save to cloud"}</button></div></div>
          {!editable.isLoading && !editable.data && markdown.length === 0 ? <div className="note-starters"><p>Start with a blank page or copy generated material into an editable note.</p><button type="button" onClick={() => initialize.mutate("BLANK")}>Blank note</button><button type="button" disabled={source.status !== "READY"} onClick={() => initialize.mutate("RAW")}>Parsed text</button><button type="button" disabled={source.aiNoteStatus !== "READY"} onClick={() => initialize.mutate("AI_NOTE")}>AI note</button></div> : null}
          <label className="note-title-label">Note title<input value={title} disabled={!draftReady} onChange={(event) => setTitle(event.target.value)} /></label>
          {preview ? <pre className="note-preview">{markdown || "Nothing to preview yet."}</pre> : <textarea className="note-editor" aria-label="Markdown note" value={markdown} disabled={!draftReady} onChange={(event) => setMarkdown(event.target.value)} placeholder="Write in Markdown…" />}
          <div className="editor-footer"><span>{draftReady ? "Drafts are cached on this device." : "Loading local draft…"}</span><span>{editable.data ? `Cloud save ${formatDate(editable.data.updatedAt)}` : "Not saved to cloud"}</span></div>
          {initialize.isError || save.isError ? <p className="inline-error">{initialize.error?.message ?? save.error?.message}</p> : null}
        </section>
      </div>
    </div>
  );
}

async function optionalRequest<T>(path: string): Promise<T | null> {
  try { return await apiRequest<T>(path); } catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
}

function StatusCard({ label, status, detail }: { label: string; status: string; detail: string }) {
  return <article><div><strong>{label}</strong><small>{detail}</small></div><StatusPill status={status} /></article>;
}

function formatBytes(bytes: number) { return bytes < 1_000_000 ? `${Math.round(bytes / 1_000)} KB` : `${(bytes / 1_000_000).toFixed(1)} MB`; }
function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)); }
