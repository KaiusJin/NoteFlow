import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { apiRequest } from "../lib/api";
import type { Flashcard } from "../types";

const grades = [
  { value: "AGAIN", label: "Again", hint: "1 day" },
  { value: "HARD", label: "Hard", hint: "shorter" },
  { value: "GOOD", label: "Good", hint: "normal" },
  { value: "EASY", label: "Easy", hint: "longer" }
] as const;

export default function FlashcardSessionPage() {
  const { deckId } = useParams();
  const queryClient = useQueryClient();
  const [revealed, setRevealed] = useState(false);
  const [completed, setCompleted] = useState(0);
  const resolvedDeckId = deckId ?? "";

  const cards = useQuery({ queryKey: ["flashcard-session", resolvedDeckId], queryFn: () => apiRequest<Flashcard[]>(`/flashcard-decks/${resolvedDeckId}/reviews/due?limit=100`), enabled: Boolean(resolvedDeckId) });
  const review = useMutation({
    mutationFn: ({ cardId, grade }: { cardId: string; grade: string }) => apiRequest(`/flashcards/${cardId}/reviews`, { method: "POST", body: JSON.stringify({ grade, eventId: crypto.randomUUID() }) }),
    onSuccess: async () => { setRevealed(false); setCompleted((value) => value + 1); await queryClient.invalidateQueries({ queryKey: ["flashcard-session", resolvedDeckId] }); }
  });
  const card = cards.data?.[0];

  if (!resolvedDeckId) return <Navigate to="/study" replace />;
  return <div className="page study-session-page">
    <Link className="back-link" to="/study">← Back to study</Link>
    <header className="session-header"><div><p className="eyebrow">Spaced repetition</p><h1>Flashcard review</h1></div><span>{completed} reviewed · {cards.data?.length ?? "—"} due</span></header>
    {cards.isError ? <p className="inline-error">{cards.error.message}</p> : null}
    {cards.isLoading ? <div className="page-loader">Loading due cards…</div> : null}
    {!cards.isLoading && !card ? <EmptyState icon="✓" title="Review complete">There are no more cards due in this deck.</EmptyState> : null}
    {card ? <section className={`flashcard-stage${revealed ? " revealed" : ""}`}>
      <div className="flashcard-meta"><span>{card.topic || "General"}</span><span>{card.difficulty}</span></div>
      <div className="flashcard-face"><small>{revealed ? "Answer" : "Prompt"}</small><h2>{revealed ? card.back : card.front}</h2>{!revealed && card.hint ? <p>Hint: {card.hint}</p> : null}</div>
      {!revealed ? <button className="primary-button reveal-button" type="button" onClick={() => setRevealed(true)}>Reveal answer</button> : <div className="grade-grid">{grades.map((grade) => <button key={grade.value} type="button" disabled={review.isPending} onClick={() => review.mutate({ cardId: card.id, grade: grade.value })}><strong>{grade.label}</strong><small>{grade.hint}</small></button>)}</div>}
      {review.isError ? <p className="inline-error">{review.error.message}</p> : null}
    </section> : null}
  </div>;
}
