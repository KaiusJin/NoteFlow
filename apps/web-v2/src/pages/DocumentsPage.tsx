import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageHeading } from "../components/PageHeading";
import { StatusPill } from "../components/StatusPill";
import { apiPage, apiRequest } from "../lib/api";
import type { DocumentSummary } from "../types";

interface UploadResponse { documentId: string; taskId: string; status: string }

export default function DocumentsPage() {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [documentType, setDocumentType] = useState("COURSE_NOTES");
  const [dragging, setDragging] = useState(false);

  const documents = useInfiniteQuery({
    queryKey: ["documents"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => apiPage<DocumentSummary>(`/documents?limit=24${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`),
    getNextPageParam: (page) => page.nextCursor || undefined
  });

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a PDF first.");
      const form = new FormData();
      form.set("file", file);
      form.set("documentType", documentType);
      if (title.trim()) form.set("title", title.trim());
      return apiRequest<UploadResponse>("/documents", { method: "POST", body: form, timeoutMs: 120_000 });
    },
    onSuccess: async () => {
      setFile(null);
      setTitle("");
      if (fileInput.current) fileInput.current.value = "";
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
      await queryClient.invalidateQueries({ queryKey: ["tasks"] });
    }
  });

  const items = documents.data?.pages.flatMap((page) => page.items) ?? [];

  function chooseFile(next: File | undefined) {
    if (!next) return;
    if (next.type !== "application/pdf" && !next.name.toLowerCase().endsWith(".pdf")) {
      upload.reset();
      return;
    }
    setFile(next);
    if (!title) setTitle(next.name.replace(/\.pdf$/i, ""));
  }

  function fileChanged(event: ChangeEvent<HTMLInputElement>) { chooseFile(event.target.files?.[0]); }
  function dropped(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files[0]);
  }
  function submit(event: FormEvent) { event.preventDefault(); upload.mutate(); }

  return (
    <div className="page">
      <PageHeading eyebrow="Source library" title="Documents" description="Bring in a technical PDF, then follow its progress from parsing to a study-ready source." />
      <section className="upload-panel">
        <form className="upload-form" onSubmit={submit}>
          <div
            className={`drop-zone${dragging ? " dragging" : ""}`}
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={dropped}
          >
            <input ref={fileInput} id="pdf-file" type="file" accept="application/pdf,.pdf" onChange={fileChanged} />
            <label htmlFor="pdf-file"><span className="upload-icon">↑</span><strong>{file ? file.name : "Drop a PDF here"}</strong><small>{file ? formatBytes(file.size) : "or click to browse · up to 50 MB"}</small></label>
          </div>
          <div className="upload-fields">
            <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Use the PDF filename" /></label>
            <label>Material type<select value={documentType} onChange={(event) => setDocumentType(event.target.value)}><option value="COURSE_NOTES">Course notes</option><option value="LECTURE_SLIDES">Lecture slides</option><option value="RESEARCH_PAPER">Research paper</option><option value="OTHER">Other</option></select></label>
            <button className="primary-button" type="submit" disabled={!file || upload.isPending}>{upload.isPending ? "Uploading…" : "Upload and analyze"}</button>
          </div>
          {upload.isError ? <p className="form-message error" role="alert">{upload.error.message}</p> : null}
          {upload.isSuccess ? <p className="form-message success" role="status">Upload accepted. Task {upload.data.taskId.slice(0, 8)} is queued.</p> : null}
        </form>
      </section>

      <section className="panel library-panel">
        <div className="section-heading"><div><p className="eyebrow">All sources</p><h2>Your library</h2></div><span>{items.length} loaded</span></div>
        {documents.isLoading ? <div className="skeleton-grid"><span /><span /><span /></div> : null}
        {documents.isError ? <p className="inline-error">{documents.error.message}</p> : null}
        {!documents.isLoading && items.length === 0 ? <EmptyState icon="▤" title="No documents yet">Your first uploaded source will appear here.</EmptyState> : null}
        <div className="document-grid">
          {items.map((document) => (
            <Link className="document-card document-card-link" key={document.id} to={`/documents/${document.id}`}>
              <div className="document-card-top"><span className="file-mark large">PDF</span><StatusPill status={document.status} /></div>
              <h3>{document.title}</h3>
              <p>{document.originalFilename}</p>
              <dl><div><dt>Pages</dt><dd>{document.pageCount ?? "—"}</dd></div><div><dt>Notes</dt><dd>{document.aiNoteStatus ?? "Not generated"}</dd></div></dl>
              <span className="document-open">Open workspace <span aria-hidden="true">→</span></span>
            </Link>
          ))}
        </div>
        {documents.hasNextPage ? <button className="secondary-button load-more" type="button" disabled={documents.isFetchingNextPage} onClick={() => void documents.fetchNextPage()}>{documents.isFetchingNextPage ? "Loading…" : "Load more"}</button> : null}
      </section>
    </div>
  );
}

function formatBytes(bytes: number) {
  if (bytes < 1_000_000) return `${Math.round(bytes / 1_000)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}
