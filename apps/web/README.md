# NoteFlow Web

Sidebar-based study workspace. Intentionally static: no build step, no
dependencies, and no framework migration cost before the backend workflow is
stable.

## Layout

A persistent left sidebar with four modules and a shared document selector:

| Module | Purpose |
|---|---|
| **AI Agent** | Chat-style, retrieval-grounded Q&A over your documents. Scope per message: all documents, the selected document, or a custom PDF/AI-note selection. Every answer is a set of cited evidence snippets with document, page, and similarity score. |
| **Flashcards** | Per-document flashcard decks: generate, browse all cards, and review due cards with SM-2 grades (AGAIN / HARD / GOOD / EASY). |
| **Quiz** | Per-document quiz sets: generate, take an attempt (MCQ/True-False radios, free-text answers), submit for auto + rubric-LLM grading, and review scores, feedback, and weak-topic suggestions. Grading results poll automatically. |
| **General** | Upload PDFs, watch processing tasks, and inspect documents: chunks, parsed output, markdown pages, visual regions, AI notes, and embeddings. |

The sidebar document list is shared state: selecting a document there scopes
the Flashcards and Quiz modules and the "Selected document" chat scope.

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
