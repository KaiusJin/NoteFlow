# NoteFlow / StudyForge Project Plan

## 1. Positioning

**Product name:** NoteFlow
**Concept name:** StudyForge
**One-liner:** A full-stack AI study workspace that transforms technical PDFs into citation-grounded notes, quizzes, and review checklists, then lets students refine them in a Notion-style editor with LaTeX math support.

NoteFlow targets university students and technical learners with a complete
study-material workflow: PDF upload, AI parsing, structured note generation,
quiz generation, RAG question answering, math formula editing, and
Markdown/PDF export.

Current RAG product decision:

```text
Isolated single-turn Q&A is not the final form.
The next phase is multi-turn, conversation-first RAG with streaming answers,
a context window, summary compression, explicit memory, citation validation,
and a conditional LangGraph workflow.
```

The concrete design is owned by
`docs/technical/MULTI_TURN_CONVERSATIONAL_RAG_ARCHITECTURE.md`.

The core of the project is not a simple PDF summarizer — it is a study
material platform that people can actually use and that demonstrates real
engineering ability.

## 2. Background

Students working through math, statistics, computer science, engineering,
economics, and machine learning courses handle large volumes of lecture
notes, slides, papers, and PDFs. Existing tools have recurring problems:

1. PDF readers only display; they do not organize knowledge.
2. Generic AI summarizers drop key points, mangle formatting, and cannot be
   traced back to sources.
3. Notion/Word handle math formulas, AI-generated content, and PDF source
   tracing poorly.
4. Students constantly switch between a PDF reader, ChatGPT, Notion, Word,
   and a Markdown editor.
5. AI output is only a first draft; users still edit, extend, insert
   formulas, restructure, and export by hand.

NoteFlow closes that loop:

**PDF upload -> AI notes and quizzes -> source citation verification -> rich-text editing -> LaTeX formula work -> Markdown / PDF export**

## 3. Target Users

Primary users:

1. University students
2. CS / Math / Stats / Engineering students
3. Undergraduates, research assistants, and self-learners reading papers
4. Learners converting PDFs into Markdown / Notion notes
5. Users organizing formulas, theorems, proofs, question banks, and review
   material

Typical scenarios:

1. A STAT student uploads a lecture PDF and generates notes, formula
   summaries, and practice questions.
2. A CS student uploads logic-proof notes and generates concept
   explanations, proof steps, and a quiz.
3. An ML student uploads a paper and generates a method breakdown, algorithm
   summary, and reproduction checklist.
4. A user edits AI notes in the built-in editor and exports Markdown.
5. A user inserts formulas such as `E[X] = \sum_x xP(X=x)` or
   `\operatorname{Var}(X)=E[X^2]-E[X]^2`.

## 4. Core Goals

1. Upload course PDFs, slides, papers, or lecture notes.
2. Automatically parse documents into structured study notes.
3. Automatically generate quizzes, answer explanations, and review
   checklists.
4. Generate an embedding per chunk after upload, building a searchable
   semantic index.
5. Search PDFs/notes with a single natural-language sentence.
6. Support RAG answering instead of feeding whole PDFs to the model.
7. Bind AI-generated content to PDF page numbers or text snippets wherever
   possible.
8. Provide a Notion/Word-like rich-text editor.
9. Support inline and block LaTeX in the editor.
10. Autosave edited content.
11. Export Markdown, Notion-friendly Markdown, and later PDF.
12. Deploy publicly as a portfolio project.

## 5. MVP Scope

### Must have

1. User login
2. PDF upload
3. Document list dashboard
4. Async task status display
5. PDF text parsing
6. Chunk embedding generation with pgvector storage
7. Natural-language semantic search
8. AI structured note generation
9. AI quiz generation
10. Tiptap editor
11. Inline LaTeX
12. Block LaTeX
13. Autosave
14. Markdown export
15. Docker Compose local startup
16. GitHub README and a live demo

### Explicitly out of MVP scope

1. Multi-user collaboration
2. Real-time co-editing
3. Comments
4. Full Word-grade typesetting
5. Mobile app
6. Fully local desktop runtime
7. Custom browser engine or custom WebView
8. Notion databases
9. Complex permission sharing
10. Deep handwriting-OCR optimization

## 6. Functional Requirements

### 6.1 User accounts

Users must be able to:

1. Register and log in
2. Manage their own documents
3. See upload history
4. See AI-generated notes and quizzes
5. Save edited notes
6. Export their study material

The MVP may use Clerk or NextAuth; the auth approach can change with
deployment needs.

### 6.2 Document upload

The MVP prioritizes PDF. On upload, persist:

1. Filename
2. File size
3. File type
4. Uploading user
5. Upload time
6. Storage location
7. Processing status
8. Page count
9. Document language
10. Document-type tag

### 6.3 Async task system

PDF parsing, embedding generation, and AI generation are slow; they run as
background tasks.

Capabilities:

1. Create tasks
2. Query task status
3. Worker background processing
4. Retry on failure
5. Error recording
6. Persist results on completion

Task statuses:

```text
PENDING
PROCESSING
COMPLETED
FAILED
RETRYING
CANCELLED
```

### 6.4 AI note generation

Course-notes mode:

1. Topic Overview
2. Key Definitions
3. Key Formulas
4. Important Theorems
5. Worked Examples
6. Common Mistakes
7. Practice Questions
8. Review Checklist

Paper mode:

1. Problem
2. Motivation
3. Main Contribution
4. Method
5. Algorithm Steps
6. Mathematical Formulation
7. Experiment Setup
8. Results
9. Limitations
10. Reproduction Checklist

### 6.5 RAG retrieval

The system must:

1. Parse the PDF
2. Clean the text
3. Chunk by section, page, and paragraph
4. Generate an embedding per chunk
5. Store chunks and embeddings in PostgreSQL + pgvector
6. Retrieve relevant chunks before note generation or answering
7. Generate structured answers from retrieved content

This reduces hallucination and lets users verify AI output.

### 6.6 Natural-language semantic search

The user types one natural-language sentence and searches the current PDF or
notes.

Example input:

```text
Why can variance be written as E[X^2] - E[X]^2?
```

Flow:

1. The frontend sends the query to the backend.
2. The backend or worker generates the query embedding.
3. pgvector similarity search runs over `document_chunks.embedding`.
4. The most relevant chunks are returned.
5. The frontend shows page number, section title, source snippet, and
   similarity score.

This feature does not necessarily call an LLM. Its purpose is "help the user
locate relevant material", so it should be fast, cheap, and verifiable.

Search results include:

1. chunk id
2. document id
3. page number
4. section title
5. snippet
6. similarity score

### 6.7 RAG question answering

RAG answering reuses semantic search and additionally calls an LLM for the
explanation.

Flow:

1. The user asks a question.
2. The system embeds the query.
3. pgvector retrieves top-k chunks.
4. The LLM answers from the retrieved chunks.
5. The answer carries citations.

Search and answering are separate product surfaces:

1. Search in document: find source snippets, no LLM.
2. Ask with sources: answer from sources, with LLM.

### 6.8 Citation grounding

Important AI-generated content should bind to its sources.

Each source record contains:

1. note item id
2. source document id
3. source chunk id
4. source page
5. source section
6. source text snippet

The frontend lets users click a citation to view the original snippet.

### 6.9 Note editor

Editor goals:

1. Notion/Word-like writing experience
2. AI-generated content flows into the editor
3. Manual editing
4. Headings, paragraphs, lists, quotes, code blocks
5. Inline math
6. Block math
7. Markdown export
8. Autosave
9. Edit history later

MVP editor features:

1. Heading 1 / 2 / 3
2. Paragraph
3. Bullet list
4. Numbered list
5. Code block
6. Quote
7. Inline LaTeX
8. Block LaTeX
9. Bold / Italic / Inline code
10. Undo / Redo
11. Autosave
12. Export as Markdown

### 6.10 Math support

Two formula kinds:

Inline math:

```latex
E[X] = \sum_x xP(X=x)
```

Block math:

```latex
\operatorname{Var}(X)=E[X^2]-E[X]^2
```

Interaction:

1. The user types `/math`
2. Chooses Inline Math or Block Math
3. Enters LaTeX
4. The editor renders with KaTeX
5. Clicking a formula reopens editing
6. Markdown export preserves LaTeX syntax

### 6.11 Quiz generation

Each question includes:

1. question
2. question type
3. difficulty
4. topic
5. source page
6. answer
7. explanation
8. related formula
9. common mistake

Question types:

1. Conceptual
2. Calculation
3. Proof
4. Multiple Choice
5. Short Answer
6. True / False

Difficulty:

```text
Easy
Medium
Hard
```

### 6.12 Export

MVP:

1. Markdown
2. Copy as Markdown
3. Notion-friendly Markdown

Later:

1. PDF export
2. Notion API export

Exports must preserve:

1. Heading hierarchy
2. LaTeX formulas
3. Code blocks
4. Lists
5. Citations
6. Notion-compatible formatting

## 7. Technology Stack

### 7.1 Frontend

Technologies:

1. Next.js
2. TypeScript
3. Tailwind CSS
4. shadcn/ui
5. Tiptap
6. KaTeX
7. TanStack Query
8. Zustand (optional)

Responsibilities:

1. User interface
2. Document upload
3. Dashboard
4. Task status display
5. Note editor
6. Formula editing
7. Quiz pages
8. Markdown export
9. Login state management

Rationale:

1. Next.js fits a full web app and Vercel deployment.
2. Tiptap fits an extensible Notion-style editor.
3. KaTeX renders fast, well-suited to web math.

### 7.2 Backend

Technologies:

1. Java 21
2. Spring Boot
3. Spring Security
4. Spring Data JPA
5. PostgreSQL
6. Redis
7. JWT / Clerk integration
8. Docker

Responsibilities:

1. Users and permissions
2. Document metadata management
3. File upload API
4. Task creation
5. Task status management
6. Note persistence
7. Editor content persistence
8. Quiz persistence
9. Export API
10. Worker communication

Rationale:

1. Spring Boot demonstrates classical backend competence.
2. A Java backend fits co-op / backend resume positioning.
3. REST APIs, database work, auth, and task management all fit Spring Boot.

### 7.3 AI worker

Technologies:

1. Python
2. FastAPI (optional)
3. PyMuPDF
4. pdfplumber
5. OpenAI / Gemini / Claude APIs
6. sentence-transformers (optional)
7. LangChain / LlamaIndex (optional)
8. Redis client
9. PostgreSQL client

Responsibilities:

1. Download PDFs
2. Parse text
3. Clean text
4. Chunk by section / page / paragraph
5. Generate embeddings
6. Store chunks and embeddings
7. Run RAG retrieval
8. Generate structured notes
9. Generate quizzes
10. Write results back to the database

Rationale:

1. Python has the mature PDF and AI ecosystem.
2. The worker decouples from the Java backend.
3. The architecture mirrors real production systems.

### 7.4 Database and storage

Databases:

1. PostgreSQL
2. pgvector

Cache and queue:

1. Redis

File storage:

1. MVP: local storage or Supabase Storage
2. Production: Cloudflare R2 or AWS S3

### 7.5 Desktop

Recommended desktop route:

```text
Build the web app first
  -> deploy the hosted demo
  -> then wrap it as a desktop app with Electron
```

Recommended technologies:

1. Electron
2. TypeScript
3. Next.js static/export or a local rendering entry
4. Electron auto-updater later (optional)

Desktop responsibilities:

1. Installable macOS / Windows / Linux app.
2. Reuse the web UI, Tiptap editor, KaTeX math, and the PDF workflow.
3. Manage desktop windows, menus, file pickers, and local downloads.
4. V1 connects to the cloud Spring Boot backend by default.

The first desktop version does NOT:

1. Bundle PostgreSQL.
2. Bundle Redis.
3. Bundle the Java backend.
4. Bundle the Python worker.
5. Ship a custom browser engine.

Why Electron:

1. Mature support for complex web editors, PDF viewers, and React UIs.
2. Maximum reuse of the web app.
3. Better cross-platform economics than a native Swift rewrite.
4. Best effort-to-payoff ratio for portfolio purposes.

## 8. Data Model Draft

Main tables:

1. users
2. documents
3. tasks
4. document_chunks
5. notes
6. note_blocks
7. quizzes
8. quiz_questions
9. exports

### documents

```text
id
user_id
title
file_url
file_type
file_size
page_count
status
created_at
updated_at
```

### tasks

```text
id
document_id
user_id
task_type
status
progress
error_message
retry_count
created_at
started_at
completed_at
```

### document_chunks

```text
id
document_id
page_number
section_title
chunk_index
content
embedding
created_at
```

### notes

```text
id
document_id
user_id
title
content_json
content_markdown
created_at
updated_at
```

### quiz_questions

```text
id
document_id
question
answer
explanation
topic
difficulty
question_type
source_chunk_id
source_page
created_at
```

## 9. System Architecture

```text
User
  -> Next.js Frontend
  -> Java Spring Boot Backend
  -> PostgreSQL + pgvector
  -> Redis Queue
  -> Object Storage
  -> Python AI Worker
  -> LLM API
```

Core flow:

1. The user uploads a PDF.
2. The frontend calls the backend API.
3. The backend saves the file to object storage.
4. The backend creates the document record.
5. The backend creates the task record.
6. The backend pushes the task to the Redis queue.
7. The Python worker pulls the task.
8. The worker parses the PDF.
9. The worker cuts chunks.
10. The worker generates embeddings.
11. The worker stores vectors in pgvector.
12. The worker generates notes and quizzes.
13. The worker writes back to PostgreSQL.
14. The frontend shows completion.
15. The user edits notes in the editor.
16. The user exports Markdown / PDF.

## 10. Page Design

### 10.1 Landing page

1. Product introduction
2. PDF-to-notes demo
3. LaTeX editor showcase
4. RAG citation showcase
5. CTA: Start Studying

### 10.2 Dashboard

1. Recently uploaded documents
2. Tasks in progress
3. Completed notes
4. Recently edited documents
5. Quiz counts

### 10.3 Upload page

1. Drag-and-drop PDF upload
2. Document type selection
3. Output language selection
4. Output content selection

Document types:

1. Course Notes
2. Research Paper
3. Lecture Slides

Outputs:

1. Notes
2. Quiz
3. Formula Summary
4. Review Checklist

### 10.4 Task progress page

1. Upload completed
2. Parsing PDF
3. Extracting sections
4. Generating embeddings
5. Generating notes
6. Generating quiz
7. Completed

### 10.5 Document detail page

Tabs:

1. Overview
2. Notes
3. Quiz
4. Sources
5. Export

### 10.6 Editor page

Left:

1. Document outline
2. Headings

Center:

1. Tiptap editor

Right:

1. AI assistant
2. Citations
3. Source snippets

### 10.7 Quiz page

1. Grouped by topic
2. Filtered by difficulty
3. Show answers
4. View explanations
5. View source pages

## 11. Editor Data Structure

The editor's core structure is a JSON document tree:

```json
{
  "type": "doc",
  "content": [
    {
      "type": "heading",
      "attrs": { "level": 2 },
      "content": [{ "type": "text", "text": "Expected Value" }]
    },
    {
      "type": "paragraph",
      "content": [
        {
          "type": "text",
          "text": "Expected value measures the long-run average."
        }
      ]
    },
    {
      "type": "mathBlock",
      "attrs": {
        "latex": "E[X] = \\sum_x xP(X=x)"
      }
    }
  ]
}
```

Persistence strategy:

1. Editor content saved as `content_json`.
2. `content_markdown` generated alongside.
3. Autosave every 5–10 seconds.
4. Save before the user leaves the page.
5. Version history later.

Formula insertion:

1. Slash command `/math`
2. Toolbar button
3. Keyboard shortcut
4. Optional paste-LaTeX auto-detection

## 12. API Draft

Auth:

```text
POST /auth/register
POST /auth/login
GET /auth/me
```

Documents:

```text
POST /documents
GET /documents
GET /documents/{id}
DELETE /documents/{id}
```

Tasks:

```text
POST /documents/{id}/analyze
GET /tasks/{id}
GET /documents/{id}/tasks
```

Notes:

```text
GET /documents/{id}/notes
POST /documents/{id}/notes
PUT /notes/{id}
GET /notes/{id}
```

Quiz:

```text
GET /documents/{id}/quiz
POST /documents/{id}/quiz/generate
```

Chunks / Sources:

```text
GET /documents/{id}/chunks
GET /chunks/{id}
```

Semantic search:

```text
POST /documents/{id}/search
POST /documents/{id}/ask
```

Export:

```text
POST /notes/{id}/export/markdown
POST /notes/{id}/export/pdf
```

## 13. Ten-Week Roadmap

### Week 1: design and scaffolding

Goals:

1. Finalize product scope
2. Scaffold the Next.js frontend
3. Scaffold the Spring Boot backend
4. Set up PostgreSQL
5. Set up Docker Compose
6. Design the database schema

Deliverables:

1. Base project structure
2. Frontend and backend running end to end
3. Database connectivity
4. README first draft

### Week 2: users and upload

Goals:

1. Login
2. PDF upload
3. Document metadata persistence
4. Local or object storage
5. Dashboard document list

Deliverables:

1. Users can upload PDFs
2. Users can see document records
3. Backend documents API

### Week 3: async tasks

Goals:

1. Design the tasks table
2. Backend creates analysis tasks
3. Redis queue integration
4. Python worker pulls tasks
5. Frontend shows task status

Deliverables:

1. Upload automatically creates a task
2. The worker processes tasks
3. The frontend shows task status

### Week 4: PDF parsing and chunking

Goals:

1. Worker downloads PDFs
2. PyMuPDF / pdfplumber text parsing
3. Text cleaning
4. Page/paragraph chunking
5. Store in document_chunks

Deliverables:

1. PDFs parse into text blocks
2. Every chunk has a page number
3. Backend can query chunks

### Week 5: embeddings and RAG

Goals:

1. Integrate the embedding model
2. Install pgvector
3. Store chunk embeddings
4. Similarity search
5. Prepare relevant chunks for note generation

Deliverables:

1. Semantic retrieval works
2. Users can search PDFs with one sentence
3. Questions/generation tasks retrieve relevant sources
4. Initial RAG pipeline runs

### Week 6: AI notes and quizzes

Goals:

1. Design structured prompts
2. Generate course notes
3. Generate quizzes
4. Persist notes and quizzes
5. Bind source chunks

Deliverables:

1. Structured notes from uploaded PDFs
2. Quiz generation
3. Every item shows its sources

### Week 7: Notion-style editor

Goals:

1. Integrate Tiptap
2. Base rich-text features
3. Inline math
4. Block math
5. KaTeX rendering
6. Autosave

Deliverables:

1. Users can edit AI notes
2. Users can insert math
3. Content persists to the database

### Week 8: export and UI polish

Goals:

1. Markdown export
2. Notion-friendly Markdown export
3. UI polish
4. Loading / error states
5. Dashboard improvements
6. README completion

Deliverables:

1. Users can export notes
2. The project demos end to end
3. README with screenshots and architecture

### Week 9: deployment and testing

Goals:

1. Deploy the frontend
2. Deploy the backend
3. Deploy the worker
4. Deploy the database
5. Test the full flow
6. Fix bugs

Deliverables:

1. Live demo
2. Public project link
3. Demo video (optional)

### Week 10: resume packaging

Goals:

1. Technical blog post
2. README polish
3. Architecture diagrams
4. Resume bullet points
5. 1–2 minute demo recording
6. Interview walkthrough script

Deliverables:

1. Portfolio-ready project
2. Resume-ready bullet points
3. Interview-ready explanation

## 14. Deployment Plan

Recommended delivery route:

```text
Web App MVP
  -> Hosted Online Demo
  -> Electron Desktop App
```

Phase one completes the web app, because the browser version is easiest to
build, test, share, and present in interviews. Phase two deploys the hosted
demo for public access. Phase three wraps the same Next.js/TypeScript
frontend with Electron.

MVP deployment:

1. Frontend: Vercel
2. Backend: Railway / Render / Fly.io
3. Database: Supabase / Neon PostgreSQL
4. Redis: Upstash Redis
5. Storage: Cloudflare R2 / Supabase Storage
6. Worker: Railway / Render background service

Electron desktop deployment:

1. Desktop shell: Electron
2. UI: reuse the built Next.js frontend
3. Backend: the cloud Spring Boot API
4. AI pipeline: cloud worker, Redis, PostgreSQL, object storage
5. Distribution: GitHub Releases or the project site

The desktop V1 ships Cloud Mode only:

```text
Electron Desktop App
  -> loads the locally bundled web UI
  -> calls the cloud API
  -> cloud handles PDFs, embeddings, RAG, AI generation, and export
```

Local Mode is optional later. V1 should not manage Java, Python, PostgreSQL,
Redis, model API keys, and background processes on user machines.

Advanced deployment:

1. Frontend: Vercel
2. Backend: AWS ECS
3. Worker: AWS ECS
4. Database: AWS RDS PostgreSQL
5. Redis: AWS ElastiCache
6. Storage: AWS S3
7. Monitoring: CloudWatch / Prometheus / Grafana

## 15. Risks and Strategy

### 15.1 Editor complexity

Hard parts:

1. Cursor behavior
2. Formula insertion and editing
3. Markdown conversion
4. JSON editor-state storage
5. Autosave

Strategy:

1. Use Tiptap; never write an editor from scratch.
2. MVP supports only the necessary blocks.
3. No real-time collaboration yet.

### 15.2 PDF parsing complexity

Hard parts:

1. Multi-column papers
2. Headers and footers
3. Math formulas
4. Tables
5. Scanned handwritten notes

Strategy:

1. MVP prioritizes text PDFs.
2. Handwriting OCR comes later.
3. Complex formulas keep nearby source text first.
4. No deep table/image parsing initially.

### 15.3 AI reliability

Hard parts:

1. Missing key points
2. Wrong formulas
3. Fabricated content
4. Unstable question quality

Strategy:

1. Use RAG.
2. Bind every item to source chunks.
3. Let users inspect the source evidence.
4. Enforce JSON schemas.
5. Add evaluation later.

### 15.4 System complexity

Hard parts:

1. Frontend
2. Java backend
3. Python worker
4. Redis
5. PostgreSQL
6. pgvector
7. Object storage
8. LLM APIs

Strategy:

1. Phase the implementation.
2. Close the local loop with Docker Compose first.
3. Deploy to the cloud after.
4. One core module per week.

## 16. Highlights

1. Complete full-stack software
2. Java Spring Boot backend
3. Python AI worker
4. Redis async task queue
5. PostgreSQL + pgvector RAG
6. Citation-grounded generation
7. Notion-style LaTeX editor
8. Markdown / PDF export
9. A real student learning scenario
10. Deployable for real users

## 17. Resume Bullet Point Drafts

1. Built a full-stack AI study workspace that transforms technical PDFs into citation-grounded notes, quizzes, and review checklists using an asynchronous RAG pipeline.
2. Designed a Java Spring Boot backend with PostgreSQL, Redis queues, and object storage to support PDF upload, task orchestration, status tracking, retries, and result retrieval.
3. Implemented Python AI workers for PDF parsing, section-aware chunking, embedding generation, semantic retrieval with pgvector, and structured LLM generation.
4. Built a Notion-style rich-text editor with Tiptap and KaTeX, supporting inline and block LaTeX formulas, autosave, JSON-based document storage, and Markdown export.
5. Added source-grounded note and quiz generation with page-level citations, allowing users to verify AI-generated study materials against the original PDF content.

## 18. Priorities

Highest:

1. PDF upload
2. Async tasks
3. Chunk embeddings and pgvector
4. Natural-language semantic search
5. AI note generation
6. Editor
7. LaTeX formulas
8. Markdown export

Second:

1. RAG question answering
2. Citation grounding
3. Quiz generation
4. UI polish

Third:

1. PDF export
2. Notion API export
3. WebSocket real-time progress
4. Review scheduling
5. Mistake notebook
6. Collaboration

## 19. MVP Acceptance Criteria

When V1 is complete, a user can:

1. Open the site and log in.
2. Upload a text PDF.
3. Watch async processing progress.
4. Have the system parse the PDF into chunks.
5. Have embeddings generated and stored in pgvector.
6. Search PDF/note content with one sentence.
7. Ask the AI questions grounded in the retrieved sources.
8. Wait for structured note generation.
9. View the quiz and answer explanations.
10. See at least page-level citations in the notes.
11. Edit AI content in the editor.
12. Insert inline and block LaTeX.
13. Have edits autosaved.
14. Export Markdown.

## 20. Final Recommendation

This project deserves 8–10 weeks as a primary project, but the scope must be
controlled. V1's single most important outcome is the closed loop:

**PDF upload -> parse chunks -> generate embeddings -> one-sentence search -> AI notes -> editor -> formulas -> Markdown export**

After the loop closes, strengthen RAG answering, citations, quiz quality,
worker retries, PDF export, and deployment stability incrementally.

The project beats a generic AI PDF summarizer because it is a complete
product experience; it beats a generic full-stack CRUD project because it
contains an AI pipeline, RAG, async tasks, and a math editor; and it beats a
pure backend project as a portfolio because people can actually open it in a
browser and use it.

Recommended resume positioning:

**Backend / AI Infrastructure-oriented Software Engineer**
