# NoteFlow Web

Sidebar-based study workspace. The app itself runs as static files (no build
step to serve it); only the Editor module's vendored bundle is produced by a
one-off build in [../editor](../editor) (`npm install && npm run build`, output
committed under `vendor/editor/`).

## Layout

A persistent left sidebar with six modules; each module picks its documents
in place (there is no shared sidebar document list):

| Module | Purpose |
|---|---|
| **AI Agent** | Chat-style, retrieval-grounded Q&A over your documents. A "+ Sources" button under the composer opens a centered modal, grouped Markdown / AI Notes, where any mix of files can be checked as the retrieval scope (nothing checked = all sources). Every answer is a set of cited evidence snippets with document, page, and similarity score. |
| **Editor** | Notion-style Markdown editor (Milkdown Crepe: slash commands, KaTeX math, CodeMirror code blocks, tables). Open documents and standalone library notes appear as top tabs (click to switch, ✕ to close, ＋ for the home page). Toolbar: undo/redo, block type conversion, text/background colors, toggleable heading outline. Large notes load in chunks as you scroll. Document notes persist via `/documents/{id}/editable-note`; standalone notes via `/notes/{id}` — both with debounce autosave and one-click `.md` export. "Blank note" creates a standalone note in the library. |
| **Folders** | Files library backed by `/folders` (nested tree) and `/notes` (unified note model). Left panel has smart Views (All notes, AI notes, PDF markdown, Imported, My notes — cross-cutting filters by source kind) and a Folders tree. Create subfolders at any depth (＋ on any folder or the Folders header), rename/delete folders, import (`.md`/`.txt`) and export (`.md`) notes, open any note in the Editor. **Move a note by dragging its card onto a folder** (file-explorer style) — the right panel then switches into that folder. New folder / Import act on the currently selected folder. The per-document editable note is a note with `sourceDocumentId` set; a startup migration backfills legacy `document_editable_notes` rows. |
| **Flashcards** | Right-side file panel grouped Markdown / AI Notes with a per-file generate button; clicking a file shows its decks: generate, browse all cards, and review due cards with SM-2 grades (AGAIN / HARD / GOOD / EASY). |
| **Quiz** | Same right-side file panel; per-document quiz sets: generate (per-file button uses 3/5/2 difficulty defaults), take an attempt (MCQ/True-False radios, free-text answers), submit for auto + rubric-LLM grading, and review scores, feedback, and weak-topic suggestions. Grading results poll automatically. |
| **General** | Upload PDFs, watch processing tasks, and inspect documents: chunks, parsed output, markdown pages, visual regions, AI notes, and embeddings. |

## Run

From this folder:

```bash
python3 -m http.server 3000
```

Then open:

```text
http://localhost:3000
```

The page calls the API at:

```text
http://localhost:8080
```

If port `3000` is busy, another local port such as `3001` can be used. The API
allows local development origins on `localhost` and `127.0.0.1`.

To override the API base URL in the browser console:

```js
localStorage.setItem("noteflowApiBaseUrl", "http://localhost:8080");
```

When the API is unreachable, the app degrades gracefully: views render with
an explanatory message and polling backs off to every ~10 seconds.
