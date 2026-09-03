import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageHeading } from "../components/PageHeading";
import { StatusPill } from "../components/StatusPill";
import { apiPage, apiRequest } from "../lib/api";
import type { DocumentSummary, TaskSummary } from "../types";

export default function DashboardPage() {
  const documents = useQuery({
    queryKey: ["documents", "recent"],
    queryFn: () => apiPage<DocumentSummary>("/documents?limit=6")
  });
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: () => apiRequest<TaskSummary[]>("/tasks"),
    refetchInterval: (query) => query.state.data?.some((task) => task.status === "PENDING" || task.status === "PROCESSING") ? 2_000 : false
  });

  const recentDocuments = documents.data?.items ?? [];
  const activeTasks = tasks.data?.filter((task) => task.status === "PENDING" || task.status === "PROCESSING") ?? [];
  const readyDocuments = recentDocuments.filter((document) => document.status === "READY").length;

  return (
    <div className="page">
      <PageHeading
        eyebrow="Your study desk"
        title="Good to see you."
        description="Continue from a source, ask a grounded question, or turn what you read into a review session."
        actions={<Link className="primary-button link-button" to="/documents">Add a document</Link>}
      />

      <section className="metric-grid" aria-label="Workspace summary">
        <article className="metric-card accent-gold"><span>Sources</span><strong>{recentDocuments.length}</strong><small>{readyDocuments} ready to study</small></article>
        <article className="metric-card accent-mint"><span>Active tasks</span><strong>{activeTasks.length}</strong><small>{activeTasks.length ? "Working in the background" : "Workspace is caught up"}</small></article>
        <article className="metric-card accent-blue"><span>Study mode</span><strong>Focused</strong><small>Answers stay grounded in your sources</small></article>
      </section>

      <div className="dashboard-grid">
        <section className="panel">
          <div className="section-heading"><div><p className="eyebrow">Recent material</p><h2>Pick up where you left off</h2></div><Link to="/documents">View all</Link></div>
          {documents.isError ? <p className="inline-error">Could not load documents. Check the API connection.</p> : null}
          {documents.isLoading ? <div className="skeleton-list" aria-label="Loading documents"><span /><span /><span /></div> : null}
          {!documents.isLoading && recentDocuments.length === 0 ? (
            <EmptyState icon="▤" title="Your library is ready">Upload a course handout, textbook chapter, or paper to begin.</EmptyState>
          ) : (
            <div className="document-list compact-list">
              {recentDocuments.map((document) => (
                <article className="document-row" key={document.id}>
                  <span className="file-mark">PDF</span>
                  <div className="document-copy"><strong>{document.title}</strong><small>{document.pageCount ? `${document.pageCount} pages` : "Page count pending"} · {formatDate(document.createdAt)}</small></div>
                  <StatusPill status={document.status} />
                </article>
              ))}
            </div>
          )}
        </section>

        <aside className="panel task-panel">
          <div className="section-heading"><div><p className="eyebrow">Live pipeline</p><h2>Background work</h2></div></div>
          {activeTasks.length === 0 ? (
            <EmptyState icon="✓" title="All caught up">New parsing and generation work will appear here.</EmptyState>
          ) : activeTasks.map((task) => (
            <article className="task-row" key={task.id}>
              <div><strong>{task.taskType.replaceAll("_", " ")}</strong><small>{task.currentStep.replaceAll("_", " ")}</small></div>
              <span>{task.progress}%</span>
              <div className="progress-track"><i style={{ width: `${Math.max(4, task.progress)}%` }} /></div>
            </article>
          ))}
        </aside>
      </div>
    </div>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
}
