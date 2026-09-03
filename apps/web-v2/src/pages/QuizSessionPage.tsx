import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { StatusPill } from "../components/StatusPill";
import { apiRequest } from "../lib/api";
import type { QuizAttemptResult, QuizQuestion } from "../types";

interface StartAttempt { attemptId: string; status: string }
interface SubmitAttempt { attemptId: string; taskId?: string; status: string }

export default function QuizSessionPage() {
  const { quizId } = useParams();
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const resolvedQuizId = quizId ?? "";

  const questions = useQuery({ queryKey: ["quiz", resolvedQuizId, "questions"], queryFn: () => apiRequest<QuizQuestion[]>(`/quiz-sets/${resolvedQuizId}/questions`), enabled: Boolean(resolvedQuizId) });
  const start = useMutation({
    mutationFn: () => apiRequest<StartAttempt>(`/quiz-sets/${resolvedQuizId}/attempts`, { method: "POST" }),
    onSuccess: (attempt) => { setAttemptId(attempt.attemptId); setStartedAt(Date.now()); }
  });
  const submit = useMutation({
    mutationFn: async () => {
      if (!attemptId) throw new Error("Start the quiz first.");
      const elapsed = startedAt ? Math.max(0, Date.now() - startedAt) : null;
      const batch = (questions.data ?? []).map((question) => ({ questionId: question.id, response: answers[question.id] ?? "", responseTimeMs: elapsed, hintUsed: false }));
      await apiRequest(`/quiz-attempts/${attemptId}/answers`, { method: "PUT", body: JSON.stringify({ answers: batch }) });
      return apiRequest<SubmitAttempt>(`/quiz-attempts/${attemptId}/submit`, { method: "POST" });
    }
  });
  const result = useQuery({
    queryKey: ["quiz-attempt", attemptId],
    queryFn: () => apiRequest<QuizAttemptResult>(`/quiz-attempts/${attemptId}`),
    enabled: Boolean(attemptId && submit.isSuccess),
    refetchInterval: (query) => query.state.data?.attempt.status === "GRADING" ? 2_000 : false
  });
  const answered = useMemo(() => Object.values(answers).filter((answer) => answer.trim()).length, [answers]);

  if (!resolvedQuizId) return <Navigate to="/study" replace />;
  if (result.data) return <QuizResult result={result.data} />;
  return <div className="page study-session-page">
    <Link className="back-link" to="/study">← Back to study</Link>
    <header className="session-header"><div><p className="eyebrow">Grounded assessment</p><h1>Quiz session</h1></div>{attemptId ? <span>{answered}/{questions.data?.length ?? 0} answered</span> : null}</header>
    {questions.isError ? <p className="inline-error">{questions.error.message}</p> : null}
    {questions.isLoading ? <div className="page-loader">Loading questions…</div> : null}
    {!questions.isLoading && questions.data?.length === 0 ? <EmptyState icon="◇" title="No questions available">This quiz set did not produce any questions.</EmptyState> : null}
    {!attemptId && questions.data?.length ? <section className="panel quiz-intro"><h2>{questions.data.length} questions</h2><p>Answers are saved together when you submit. Short responses may be graded asynchronously by the worker.</p><button className="primary-button" type="button" disabled={start.isPending} onClick={() => start.mutate()}>{start.isPending ? "Starting…" : "Start quiz"}</button>{start.isError ? <p className="inline-error">{start.error.message}</p> : null}</section> : null}
    {attemptId && !submit.isSuccess ? <form className="quiz-form" onSubmit={(event) => { event.preventDefault(); submit.mutate(); }}>
      {(questions.data ?? []).map((question, index) => <QuestionCard key={question.id} question={question} index={index} value={answers[question.id] ?? ""} onChange={(value) => setAnswers((current) => ({ ...current, [question.id]: value }))} />)}
      <div className="quiz-submit"><span>Unanswered questions will be submitted blank.</span><button className="primary-button" type="submit" disabled={submit.isPending}>{submit.isPending ? "Submitting…" : "Submit quiz"}</button></div>
      {submit.isError ? <p className="inline-error">{submit.error.message}</p> : null}
    </form> : null}
    {submit.isSuccess && !result.data ? <div className="page-loader"><span>Grading your answers…</span></div> : null}
  </div>;
}

function QuestionCard({ question, index, value, onChange }: { question: QuizQuestion; index: number; value: string; onChange: (value: string) => void }) {
  const options = parseOptions(question.options_json);
  return <fieldset className="panel question-card"><legend>Question {index + 1}</legend><div className="question-meta"><StatusPill status={question.difficulty} /><span>{question.points} point{question.points === 1 ? "" : "s"}</span>{question.topic ? <span>{question.topic}</span> : null}</div><h2>{question.stem}</h2>{options.length ? <div className="answer-options">{options.map((option) => <label key={option}><input type="radio" name={question.id} value={option} checked={value === option} onChange={(event) => onChange(event.target.value)} /><span>{option}</span></label>)}</div> : <textarea value={value} onChange={(event) => onChange(event.target.value)} rows={4} placeholder="Write your answer…" />}</fieldset>;
}

function QuizResult({ result }: { result: QuizAttemptResult }) {
  const completed = result.attempt.status === "COMPLETED";
  return <div className="page study-session-page"><Link className="back-link" to="/study">← Back to study</Link><header className="session-header"><div><p className="eyebrow">Quiz result</p><h1>{completed ? `${result.attempt.score ?? 0} / ${result.attempt.max_score ?? 0}` : "Grading in progress"}</h1></div><StatusPill status={result.attempt.status} /></header><div className="quiz-results">{result.answers.map((answer, index) => <article className={`panel answer-result${answer.is_correct === true ? " correct" : answer.is_correct === false ? " incorrect" : ""}`} key={answer.question_id}><span>Question {index + 1}</span><h2>{answer.stem}</h2><p><strong>Your answer:</strong> {answer.user_response || "No answer"}</p>{answer.correct_answer ? <p><strong>Correct answer:</strong> {answer.correct_answer}</p> : null}{answer.feedback || answer.explanation ? <p className="feedback">{answer.feedback || answer.explanation}</p> : null}</article>)}</div></div>;
}

function parseOptions(value: string | null): string[] {
  if (!value) return [];
  try { const parsed: unknown = JSON.parse(value); return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : []; } catch { return []; }
}
