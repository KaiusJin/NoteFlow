import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageHeading } from "../components/PageHeading";
import { StatusPill } from "../components/StatusPill";
import { apiPage, apiRequest } from "../lib/api";
import type { DocumentSummary, StudySet } from "../types";

export default function StudyPage() {
  const queryClient = useQueryClient();
  const [documentId, setDocumentId] = useState("");
  const documents = useQuery({ queryKey: ["documents", "study"], queryFn: () => apiPage<DocumentSummary>("/documents?limit=100") });
  const decks = useQuery({ queryKey: ["study", documentId, "decks"], queryFn: () => apiRequest<StudySet[]>(`/documents/${documentId}/flashcard-decks`), enabled: Boolean(documentId) });
  const quizzes = useQuery({ queryKey: ["study", documentId, "quizzes"], queryFn: () => apiRequest<StudySet[]>(`/documents/${documentId}/quiz-sets`), enabled: Boolean(documentId) });

  const generate = useMutation({
    mutationFn: (kind: "flashcards" | "quiz") => apiRequest<Record<string, unknown>>(
      kind === "flashcards" ? `/documents/${documentId}/flashcard-decks` : `/documents/${documentId}/quiz-sets`,
      { method: "POST", body: JSON.stringify(kind === "flashcards" ? { count: 24, groupBySection: true } : { easy: 4, medium: 6, hard: 2, includeExplanations: true }) }
    ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["study", documentId] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] })
      ]);
    }
  });

  const readyDocuments = (documents.data?.items ?? []).filter((document) => document.status === "READY");

  return (
    <div className="page">
      <PageHeading eyebrow="Active recall" title="Study" description="Convert a source into focused cards and questions, then return when they are due." />
      <section className="study-source panel">
        <label>Study source<select value={documentId} onChange={(event) => setDocumentId(event.target.value)}><option value="">Choose a ready document</option>{readyDocuments.map((document) => <option key={document.id} value={document.id}>{document.title}</option>)}</select></label>
        <div className="study-actions">
          <button className="primary-button" type="button" disabled={!documentId || generate.isPending} onClick={() => generate.mutate("flashcards")}>Generate flashcards</button>
          <button className="secondary-button" type="button" disabled={!documentId || generate.isPending} onClick={() => generate.mutate("quiz")}>Build a quiz</button>
        </div>
        <p className="field-note">Default generation is intentionally capped: 24 cards or 12 questions.</p>
        {generate.isError ? <p className="form-message error">{generate.error.message}</p> : null}
      </section>

      {!documentId ? <EmptyState icon="◇" title="Choose what to practice">Only parsed, ready documents are offered as study sources.</EmptyState> : (
        <div className="study-columns">
          <StudySetList title="Flashcard decks" empty="No flashcard decks yet." items={decks.data ?? []} kind="flashcards" />
          <StudySetList title="Quizzes" empty="No quizzes yet." items={quizzes.data ?? []} kind="quizzes" />
        </div>
      )}
    </div>
  );
}

function StudySetList({ title, empty, items, kind }: { title: string; empty: string; items: StudySet[]; kind: "flashcards" | "quizzes" }) {
  return <section className="panel"><div className="section-heading"><div><p className="eyebrow">Generated sets</p><h2>{title}</h2></div></div>{items.length === 0 ? <p className="muted-copy">{empty}</p> : items.map((item) => item.status === "READY" ? <Link className="study-set study-set-link" key={item.id} to={`/study/${kind}/${item.id}`}><div><strong>{item.title}</strong><small>Version {item.version}</small></div><span><StatusPill status={item.status} /> <i aria-hidden="true">→</i></span></Link> : <article className="study-set" key={item.id}><div><strong>{item.title}</strong><small>Version {item.version}</small></div><StatusPill status={item.status} /></article>)}</section>;
}
