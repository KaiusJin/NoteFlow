import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { EmptyState } from "../components/EmptyState";
import { PageHeading } from "../components/PageHeading";
import { apiPage, apiRequest } from "../lib/api";
import type { DocumentSummary, SearchResponse } from "../types";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<string[]>([]);
  const documents = useQuery({ queryKey: ["documents", "search-scope"], queryFn: () => apiPage<DocumentSummary>("/documents?limit=100") });
  const search = useMutation({
    mutationFn: () => apiRequest<SearchResponse>("/search", {
      method: "POST",
      body: JSON.stringify({ query: query.trim(), topK: 12, mode: "MIXED", pdfDocumentIds: scope, aiNoteDocumentIds: scope })
    })
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (query.trim()) search.mutate();
  }

  function toggleDocument(id: string) {
    setScope((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }

  return (
    <div className="page search-page">
      <PageHeading eyebrow="Evidence finder" title="Search your sources" description="Find the exact passage first. Use the Agent when you want a synthesized answer." />
      <form className="search-bar" onSubmit={submit}>
        <span aria-hidden="true">⌕</span>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="What does the source say about…" aria-label="Search query" />
        <button className="primary-button" disabled={!query.trim() || search.isPending}>{search.isPending ? "Searching…" : "Search"}</button>
      </form>
      <details className="scope-picker">
        <summary>{scope.length ? `${scope.length} source${scope.length === 1 ? "" : "s"} selected` : "Search across every source"}</summary>
        <div className="scope-options">
          {(documents.data?.items ?? []).map((document) => (
            <label key={document.id}><input type="checkbox" checked={scope.includes(document.id)} onChange={() => toggleDocument(document.id)} />{document.title}</label>
          ))}
        </div>
      </details>
      {search.isError ? <p className="inline-error">{search.error.message}</p> : null}
      {!search.data && !search.isPending ? <EmptyState icon="⌕" title="Search before you ask">A focused search lets you inspect passages and page numbers without generating an answer.</EmptyState> : null}
      {search.data ? (
        <section className="search-results" aria-live="polite">
          <div className="section-heading"><div><p className="eyebrow">{search.data.results.length} passages</p><h2>Results for “{search.data.query}”</h2></div></div>
          {search.data.results.length === 0 ? <EmptyState icon="∅" title="No grounded matches">Try fewer terms or widen the source scope.</EmptyState> : null}
          {search.data.results.map((result, index) => (
            <article className="result-card" key={`${result.sourceObjectId}-${index}`}>
              <div className="result-meta"><span>{result.sourceDomain.replaceAll("_", " ")}</span><span>{pageLabel(result.pageStart, result.pageEnd)}</span><strong>{Math.round(result.score * 100)}%</strong></div>
              <h3>{result.title || "Untitled passage"}</h3><p>{result.snippet}</p>
            </article>
          ))}
        </section>
      ) : null}
    </div>
  );
}

function pageLabel(start: number | null, end: number | null) {
  if (!start) return "Notes";
  return end && end !== start ? `Pages ${start}–${end}` : `Page ${start}`;
}
