# NoteFlow Workflow and Architecture

This document explains NoteFlow's end-to-end workflow, what each step does,
the system architecture, and how the frontend, backend, worker, database,
Redis, object storage, and LLM APIs connect.

> Current implementation note:
> The consolidated current technical specification is `docs/technical/NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md`.
> This document still includes roadmap items such as embeddings, quiz generation, and production object storage. When this document differs from the current implementation, use the consolidated specification as the source of truth.
>
> RAG architecture update:
> The old `POST /documents/{id}/ask` sections describe an earlier stateless
> draft. The next implementation is conversation-first and multi-turn. Use
> `docs/technical/MULTI_TURN_CONVERSATIONAL_RAG_ARCHITECTURE.md` as the source
> of truth for conversation APIs, streaming, memory, prompt strategy, LangGraph,
> local SLM deployment, and context compression.

## 0. Currently Implemented Main Chain

```text
Web App
  -> Spring Boot API
  -> local upload storage
  -> PostgreSQL documents/tasks
  -> Redis queue
  -> Python worker
  -> PDF page rendering + visual regions + VLM
  -> page Markdown + document Markdown
  -> strategy-aware chunks
  -> resumable AI notes generation
  -> final AI notes Markdown
  -> exported_ai_notes/*.md
```

Key constraints of the current implementation:

1. The API owns reliable task creation and enqueueing; enqueueing happens
   after the database transaction commits.
2. The worker processes at most 3 tasks concurrently.
3. A single AI-notes task sends at most 3 provider requests concurrently.
4. A failed AI-note group never discards already-saved sections; retries
   resume from the checkpoint.
5. The final AI-notes Markdown can be rebuilt offline from saved sections
   without repeating any AI API calls.

## 1. Overall Goal

NoteFlow's core loop:

```text
User uploads a PDF
  -> the system stores the file and the document record
  -> an async analysis task is created
  -> the worker parses the PDF
  -> the worker chunks the text and generates embeddings
  -> the worker calls the LLM to generate notes and quizzes
  -> the backend persists the results
  -> the user searches PDF/notes with one sentence
  -> the user asks the AI questions grounded in search results
  -> the user edits in the editor
  -> the user exports Markdown / PDF
```

V1's single most important outcome is closing this loop. Embeddings and
natural-language search are core capabilities: uploading builds the semantic
index; searching converts the user's sentence into a query embedding and uses
pgvector to find relevant PDF source text and notes.

## 2. System Architecture

### 2.1 Services

```text
Frontend
  Next.js + TypeScript + Tiptap + KaTeX

Desktop Shell
  Electron + TypeScript
  Reuses the Web App UI after the hosted demo is working

Backend API
  Java 21 + Spring Boot + Spring Security + Spring Data JPA

Database
  PostgreSQL + pgvector

Queue / Cache
  Redis

AI Worker
  Python + PyMuPDF/pdfplumber + LLM API + embedding model

Object Storage
  Local storage for MVP
  Cloudflare R2 / AWS S3 / Supabase Storage for production
```

### 2.2 Architecture diagram

```text
                               +------------------+
                               |     LLM API      |
                               | OpenAI/Gemini/...|
                               +---------^--------+
                                         |
                                         |
+--------+      HTTPS       +-----------+----------+
|  User  +----------------->|   Next.js Frontend   |
+--------+                  +-----------+----------+
                                         |
                                         | REST API
                                         v
                              +----------+-----------+
                              | Spring Boot Backend  |
                              +----+-----------+-----+
                                   |           |
                         SQL/JPA   |           | Redis commands
                                   v           v
                         +---------+--+     +--+---------+
                         | PostgreSQL |     |   Redis    |
                         | + pgvector |     |   Queue    |
                         +-----+------+     +--+---------+
                               ^               |
                               |               | task pop / status update
                               |               v
                         +-----+---------------+-----+
                         |      Python AI Worker     |
                         +-----+---------------+-----+
                               |
                               | read/write files
                               v
                         +-----+---------------+
                         |   Object Storage    |
                         +---------------------+
```

The desktop app is not a custom browser engine; it is an Electron shell:

```text
User
  -> Electron Desktop App
  -> bundled Next.js UI
  -> Cloud Spring Boot Backend
  -> Cloud Worker / PostgreSQL / Redis / Object Storage
```

The desktop V1 uses Cloud Mode. Local Mode is a later consideration.

## 3. Service Responsibilities

### 3.1 Frontend

The frontend owns everything the user sees and touches.

Responsibilities:

1. Login and session state
2. Dashboard document list
3. PDF upload form
4. Task progress display
5. Document detail page
6. Note editor
7. Quiz pages
8. Natural-language search box
9. RAG question-answering entry point
10. Citation / source snippet display
11. Markdown export button

The frontend calls only the Backend API — never the database, Redis, or the
worker directly.

### 3.1.1 Desktop shell

The desktop shell wraps the web app into installable software.

Responsibilities:

1. Create desktop windows.
2. Load the bundled Next.js UI.
3. Provide menus, file pickers, and download locations.
4. Call the same cloud Backend API.
5. Auto-updates later.

The desktop V1 does NOT:

1. Bundle the Java backend.
2. Bundle the Python worker.
3. Bundle PostgreSQL.
4. Bundle Redis.
5. Ship a custom browser engine.

Recommended order:

```text
Build the web app
  -> deploy the hosted demo
  -> then build the Electron desktop app
```

### 3.2 Backend API

The backend is the system's control center.

Responsibilities:

1. Authentication and authorization
2. Receiving uploads
3. Saving files to object storage
4. Creating document records
5. Creating task records
6. Pushing tasks to the Redis queue
7. Task status query APIs
8. Notes, quiz, and chunk query APIs
9. Natural-language semantic search API
10. RAG question-answering API
11. Persisting editor content
12. Markdown / PDF export

The backend never performs slow AI analysis itself; slow work belongs to the
worker.

### 3.3 AI worker

The worker handles slow tasks exclusively.

Responsibilities:

1. Pull tasks from the Redis queue
2. Look up the document by task id
3. Download the PDF from object storage
4. Parse PDF text
5. Clean text
6. Cut chunks
7. Generate embeddings
8. Write document_chunks
9. Generate query embeddings for user queries
10. Retrieve relevant chunks
11. Call the LLM for structured notes
12. Call the LLM for quizzes
13. Call the LLM for source-grounded answers
14. Persist notes and quiz_questions
15. Update task status to COMPLETED or FAILED

The worker never handles user permissions or page rendering.

### 3.4 PostgreSQL + pgvector

The database persists core business data:

1. Users
2. Document metadata
3. Task status
4. PDF chunks
5. Embeddings
6. Source chunks behind semantic search results
7. Note JSON
8. Note Markdown
9. Quizzes
10. Citation sources
11. Export records

pgvector performs semantic similarity retrieval.

### 3.5 Redis

Redis serves primarily as the task queue in V1:

1. Holds pending task ids
2. Workers pull tasks
3. Caches task progress
4. Prevents duplicate submission
5. Rate limiting later

### 3.6 Object storage

Object storage holds large files:

1. Original PDFs
2. Exported Markdown files
3. Exported PDF files
4. Future image assets

The MVP can use a local folder such as `storage/uploads`, switching to R2,
S3, or Supabase Storage at deployment.

## 4. How Data Connects

### 4.1 Frontend to backend

REST API:

```text
Frontend -> Backend

POST /documents
GET /documents
GET /tasks/{id}
GET /documents/{id}/notes
PUT /notes/{id}
POST /notes/{id}/export/markdown
```

Requests carry credentials:

```text
Authorization: Bearer <access_token>
```

### 4.2 Backend to database

Spring Boot connects to PostgreSQL via Spring Data JPA.

Example environment variables:

```env
SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:5432/noteflow
SPRING_DATASOURCE_USERNAME=noteflow
SPRING_DATASOURCE_PASSWORD=noteflow
```

The backend writes through repositories/services:

1. documents
2. tasks
3. notes
4. quiz_questions
5. exports

### 4.3 Backend to Redis

After creating a task, the backend pushes the task id to Redis.

Example queue:

```text
queue:document-analysis
```

Payload:

```json
{
  "taskId": "task_123",
  "documentId": "doc_456",
  "userId": "user_789",
  "taskType": "ANALYZE_DOCUMENT"
}
```

### 4.4 Worker to Redis

The worker pulls tasks from the queue:

```text
BRPOP queue:document-analysis
  -> parse task payload
  -> mark task PROCESSING
  -> run pipeline
  -> mark task COMPLETED or FAILED
```

### 4.5 Worker to database

Reads:

1. task
2. document
3. file_url

Writes:

1. document_chunks
2. notes
3. quiz_questions
4. task status
5. error_message

### 4.6 Worker to object storage

The worker downloads the PDF via document.file_url.

MVP local storage:

```text
storage/uploads/{document_id}.pdf
```

Production object storage:

```text
s3://noteflow/uploads/{document_id}.pdf
```

### 4.7 Worker to LLM APIs

The worker calls models for three kinds of work:

1. Embedding: turning chunks into vectors
2. Query embedding: turning the user's one-sentence search into a vector
3. Generation: notes, quizzes, summaries, checklists, and source-grounded
   answers

All LLM output should use JSON schemas wherever possible so the backend can
persist it reliably.

## 4.8 How Search and Answering Connect

Semantic search and RAG answering share one retrieval foundation but are two
distinct product capabilities.

Semantic search:

```text
User types one sentence
  -> generate query embedding
  -> pgvector similarity search
  -> return matching chunks
  -> frontend shows source snippets, page numbers, sections, similarity
```

RAG answer:

```text
User asks a question
  -> generate query embedding
  -> pgvector similarity search
  -> send top-k chunks + question to the LLM
  -> return answer with citations
```

Search does not necessarily call an LLM — its goal is locating material.
Answering calls the LLM — its goal is explaining from material.

## 5. Full User Workflow

### Step 1: login

User actions:

1. Open the site
2. Click login
3. Sign in with email, Google, or a third-party auth provider

Frontend:

1. Calls the auth service
2. Stores the session
3. Obtains the access token
4. Redirects to the dashboard

Backend:

1. Validates the token
2. Creates or updates the user record
3. Returns the current user

APIs:

```text
GET /auth/me
```

Tables:

```text
users
```

### Step 2: upload a PDF

User actions:

1. Open the Upload page
2. Drag or select a PDF
3. Choose the document type
4. Choose the output language
5. Check Notes / Quiz / Checklist outputs
6. Click Upload

Frontend:

1. Validates the file type is PDF
2. Validates file size
3. Builds a multipart/form-data request
4. Calls `POST /documents`
5. Redirects to the task progress page

Backend:

1. Authenticates the user
2. Receives the PDF
3. Saves the file to storage
4. Creates the documents record
5. Creates the tasks record
6. Pushes the task to the Redis queue
7. Returns the document id and task id

APIs:

```text
POST /documents
```

Tables:

```text
documents
tasks
```

Redis queue:

```text
queue:document-analysis
```

### Step 3: task progress display

User actions:

1. Lands on the progress page after upload
2. Waits for processing
3. Sees the current step and percentage

Frontend:

1. Polls `GET /tasks/{id}`
2. Displays task.status
3. Displays task.progress
4. Redirects to the Document Detail page on COMPLETED
5. Shows the error and a retry button on FAILED

Backend:

1. Queries the tasks table
2. Returns task status

APIs:

```text
GET /tasks/{id}
```

Statuses:

```text
PENDING
PROCESSING
COMPLETED
FAILED
RETRYING
CANCELLED
```

### Step 4: worker parses the PDF

Trigger:

1. The backend pushed the task to Redis
2. The worker pulls it

Worker:

1. Reads the task payload
2. Sets task.status = PROCESSING
3. Looks up document.file_url
4. Downloads the PDF
5. Parses text with PyMuPDF or pdfplumber
6. Extracts page numbers
7. Cleans headers, footers, repeated whitespace
8. Saves intermediates or proceeds directly to chunking

Tables:

```text
tasks
documents
```

Failure handling:

1. On parse failure, write error_message
2. retry_count + 1
3. Re-enqueue below the retry cap
4. Mark FAILED beyond it

### Step 5: worker cuts chunks

Worker:

1. Splits text by page
2. Attempts section-title detection
3. Chunks by paragraph or token length
4. Keeps page_number per chunk
5. Saves to document_chunks

Each chunk carries:

```text
document_id
page_number
section_title
chunk_index
content
```

Tables:

```text
document_chunks
```

### Step 6: worker generates embeddings

Worker:

1. Iterates document_chunks
2. Calls the embedding model
3. Receives vectors
4. Saves to document_chunks.embedding
5. Marks the document's semantic index as built

Technology:

```text
pgvector
```

Conceptual query:

```sql
SELECT *
FROM document_chunks
WHERE document_id = :documentId
ORDER BY embedding <-> :queryEmbedding
LIMIT 8;
```

### Step 7: worker runs RAG retrieval

Worker:

1. Builds a generation query from the document type
2. Embeds the query
3. Retrieves relevant chunks from pgvector
4. Uses the chunks as source context
5. Passes them to the LLM

Purpose:

1. Reduce hallucination
2. Improve relevance
3. Prepare for citation grounding
4. Reuse one semantic index for user search and answering

### Step 8: worker generates structured notes

Worker:

1. Selects the prompt by document type
2. Sends source chunks to the LLM
3. Requires JSON output
4. Validates the JSON schema
5. Converts to Tiptap content_json
6. Generates content_markdown
7. Saves to notes

Note structure:

```text
Topic Overview
Key Definitions
Key Formulas
Important Theorems
Worked Examples
Common Mistakes
Practice Questions
Review Checklist
```

Tables:

```text
notes
note_blocks
document_chunks
```

### Step 9: worker generates the quiz

Worker:

1. Generates questions from chunks and notes
2. Requires JSON output
3. Saves question, answer, explanation, difficulty, source_page per question
4. Writes quiz_questions

Question types:

```text
Conceptual
Calculation
Proof
Multiple Choice
Short Answer
True / False
```

Tables:

```text
quizzes
quiz_questions
document_chunks
```

### Step 10: worker completes the task

Worker:

1. All results persisted
2. task.progress = 100
3. task.status = COMPLETED
4. completed_at written

The frontend's next poll sees COMPLETED and redirects to the document detail
page.

### Step 11: user views the document

User actions:

1. Opens the Document Detail page
2. Views Overview
3. Views Notes
4. Views Quiz
5. Views Sources
6. Clicks citations to view source snippets

Frontend:

1. Calls the document API
2. Calls the notes API
3. Calls the quiz API
4. Calls the chunks API
5. Renders tabs

APIs:

```text
GET /documents/{id}
GET /documents/{id}/notes
GET /documents/{id}/quiz
GET /documents/{id}/chunks
```

### Step 12: user searches PDF/notes with one sentence

User actions:

1. Types one natural-language sentence in the detail page or the assistant
   panel — e.g. `Why can variance be written as E[X^2] - E[X]^2?`
2. Clicks Search in document
3. Reviews matching pages, sections, and source snippets

Frontend:

1. Calls `POST /documents/{id}/search`
2. Sends query and limit
3. Renders matching chunks
4. Shows page number, section title, snippet, and score
5. Opens the source panel on click

Backend:

1. Verifies document access
2. Generates the query embedding (or asks the worker/AI service to)
3. Runs pgvector top-k search over document_chunks
4. Returns results

Request example:

```json
{
  "query": "explain the variance shortcut formula",
  "limit": 8
}
```

Response example:

```json
{
  "results": [
    {
      "chunkId": "chunk_123",
      "pageNumber": 4,
      "sectionTitle": "Variance",
      "snippet": "The variance can be computed using E[X^2] - E[X]^2...",
      "score": 0.83
    }
  ]
}
```

APIs:

```text
POST /documents/{id}/search
```

Tables:

```text
document_chunks
```

### Step 13: user asks the AI with sources

User actions:

1. Types a question in the assistant
2. Clicks Ask with sources
3. Reads the AI answer
4. Clicks citations to view the source text

Frontend:

1. Calls `POST /documents/{id}/ask`
2. Displays the answer
3. Displays citations
4. Lets the user insert the answer into the editor

Backend or worker:

1. Verifies permissions
2. Embeds the question
3. Retrieves top-k source chunks from pgvector
4. Sends the question and sources to the LLM
5. Requires answering only from the sources
6. Returns the answer and citations

Request example:

```json
{
  "question": "Why does variance equal E[X^2] - E[X]^2?",
  "limit": 8
}
```

Response example:

```json
{
  "answer": "Variance is defined as E[(X - E[X])^2]. Expanding the square gives E[X^2] - E[X]^2...",
  "citations": [
    {
      "chunkId": "chunk_123",
      "pageNumber": 4,
      "snippet": "..."
    }
  ]
}
```

APIs:

```text
POST /documents/{id}/ask
```

Tables:

```text
document_chunks
```

### Step 14: user edits in the editor

User actions:

1. Clicks Edit Notes
2. Modifies AI content
3. Inserts headings, lists, quotes, code blocks
4. Inserts inline math
5. Inserts block math

Frontend:

1. Loads note.content_json
2. Initializes the Tiptap editor
3. Renders formulas with KaTeX
4. Updates local editor state as the user types
5. Autosaves every 5–10 seconds

Backend:

1. Receives `PUT /notes/{id}`
2. Verifies note ownership
3. Saves content_json
4. Saves content_markdown alongside
5. Updates updated_at

APIs:

```text
GET /notes/{id}
PUT /notes/{id}
```

Tables:

```text
notes
```

### Step 15: user exports Markdown

User actions:

1. Clicks Export
2. Chooses Markdown
3. Downloads or copies the Markdown

Frontend:

1. Calls the export API
2. Shows the download button or copy output

Backend:

1. Reads note.content_json or content_markdown
2. Converts to Markdown
3. Preserves headings, lists, code blocks, LaTeX, citations
4. Returns Markdown text or a file URL
5. Creates the export record

APIs:

```text
POST /notes/{id}/export/markdown
```

Tables:

```text
exports
```

## 6. Backend Internal Layering

Recommended Spring Boot layering:

```text
controller
  HTTP requests, parameters, responses

service
  Business logic: create documents, create tasks, save notes

repository
  Database access

entity
  JPA entities

dto
  Request and response objects

security
  Authentication and authorization

storage
  File storage interface

queue
  Redis queue interface

export
  Markdown / PDF export logic
```

Example modules:

```text
com.noteflow.auth
com.noteflow.documents
com.noteflow.tasks
com.noteflow.notes
com.noteflow.quiz
com.noteflow.chunks
com.noteflow.storage
com.noteflow.queue
com.noteflow.export
```

## 7. Worker Internal Layering

Recommended Python worker layering:

```text
worker/
  main.py
  config.py
  queue/
    redis_client.py
    task_consumer.py
  db/
    postgres.py
    repositories.py
  storage/
    local_storage.py
    s3_storage.py
  pdf/
    parser.py
    cleaner.py
    chunker.py
  ai/
    embeddings.py
    prompts.py
    generator.py
    schemas.py
  pipelines/
    analyze_document.py
```

Core pipeline:

```text
consume task
  -> load document
  -> download pdf
  -> parse pdf
  -> clean text
  -> chunk text
  -> store chunks
  -> generate embeddings
  -> retrieve context
  -> generate notes
  -> generate quiz
  -> save results
  -> complete task
```

## 8. Page-to-API Mapping

```text
/login
  GET /auth/me

/dashboard
  GET /documents
  GET /tasks/recent

/upload
  POST /documents

/tasks/[taskId]
  GET /tasks/{id}

/documents/[documentId]
  GET /documents/{id}
  GET /documents/{id}/notes
  GET /documents/{id}/quiz
  POST /documents/{id}/search
  POST /documents/{id}/ask

/documents/[documentId]/editor
  GET /notes/{id}
  PUT /notes/{id}

/documents/[documentId]/sources
  GET /documents/{id}/chunks

/documents/[documentId]/export
  POST /notes/{id}/export/markdown
```

## 9. Local Development Connectivity

### 9.1 Suggested local ports

```text
Frontend:        http://localhost:3000
Backend API:     http://localhost:8080
PostgreSQL:      localhost:5432
Redis:           localhost:6379
Worker:          background process
Object Storage:  ./storage
```

Electron Cloud Mode during development:

```text
Electron App:    local desktop shell
Web UI:          bundled Next.js build
Backend API:     deployed cloud API or http://localhost:8080
AI Worker:       cloud worker or local worker
Database/Redis:  cloud services or local Docker services
```

### 9.2 Docker Compose targets

V1 Docker Compose starts:

1. postgres
2. redis
3. backend
4. worker
5. frontend (optional)

Electron stays out of the V1 Compose file; the desktop app is developed
separately once the web app and hosted demo are stable.

Local development can also run:

1. postgres + redis in Docker
2. the frontend on the host
3. the backend on the host
4. the worker on the host

### 9.3 Environment variables

Frontend:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8080
```

Backend:

```env
SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:5432/noteflow
SPRING_DATASOURCE_USERNAME=noteflow
SPRING_DATASOURCE_PASSWORD=noteflow
REDIS_URL=redis://localhost:6379
STORAGE_TYPE=local
STORAGE_LOCAL_DIR=./storage
```

Worker:

```env
DATABASE_URL=postgresql://noteflow:noteflow@localhost:5432/noteflow
REDIS_URL=redis://localhost:6379
STORAGE_TYPE=local
STORAGE_LOCAL_DIR=./storage
LLM_API_KEY=replace_me
EMBEDDING_MODEL=replace_me
```

## 10. MVP Implementation Order

### Phase 1: skeleton

Do:

1. Create frontend, backend, worker directories
2. Configure Docker Compose
3. Start PostgreSQL and Redis
4. Connect the backend to the database
5. Frontend calls the backend health check

Done when:

1. `GET /health` returns OK
2. The frontend shows "API connected"
3. Database migrations run

### Phase 2: document upload

Do:

1. Frontend PDF upload
2. Backend receives the multipart file
3. Save to local storage
4. Write the documents table
5. Return the document id

Done when:

1. Users can upload PDFs
2. The dashboard lists documents
3. PDFs appear in the storage directory

### Phase 3: task system

Do:

1. Create the tasks table
2. Create an analysis task on upload
3. Push to the Redis queue
4. Worker consumes the task
5. Worker updates task status

Done when:

1. Upload produces a PENDING task
2. Consumption flips it to PROCESSING
3. Completion flips it to COMPLETED

### Phase 4: PDF parsing and chunks

Do:

1. Worker downloads the PDF
2. Parses text
3. Splits by page
4. Writes document_chunks
5. Backend chunk query API

Done when:

1. The Sources page shows page numbers and chunk text
2. A PDF parses into multiple chunks

### Phase 5: embeddings and the pgvector index

Do:

1. Integrate the embedding model
2. Enable pgvector
3. Generate chunk embeddings
4. Store in document_chunks.embedding
5. Implement similarity search

Done when:

1. Every chunk of an uploaded PDF has an embedding
2. The backend searches chunks by query embedding
3. The Sources page returns semantically ranked results

### Phase 6: natural-language search and RAG answering

Do:

1. Implement `POST /documents/{id}/search`
2. Embed user queries
3. pgvector top-k search
4. Frontend shows source snippets
5. Implement `POST /documents/{id}/ask`
6. Send top-k chunks and the question to the LLM
7. Return the answer with citations

Done when:

1. One-sentence search over PDF content works
2. Results include page, section, snippet, similarity
3. Users can ask document-grounded questions
4. Answers carry citations

### Phase 7: AI note generation

Do:

1. Worker calls the LLM
2. Generates structured JSON
3. Converts to note content_json
4. Saves the notes table
5. Frontend renders notes
6. Key content binds to source chunks

Done when:

1. An uploaded PDF yields a structured note
2. The Notes page renders headings, paragraphs, lists correctly
3. Key content traces back to source chunks

### Phase 8: editor and LaTeX

Do:

1. Integrate Tiptap
2. Base rich-text extensions
3. Inline math
4. Block math
5. KaTeX rendering
6. Autosave

Done when:

1. Users can edit AI notes
2. Users can insert inline formulas
3. Users can insert block formulas
4. Content survives a refresh

### Phase 9: Markdown export

Do:

1. content_json to Markdown
2. Preserve LaTeX
3. Preserve citations
4. Provide download or copy

Done when:

1. Users can export Markdown
2. Exports import cleanly into Notion or Markdown editors

### Phase 10: citations and product polish

Do:

1. Improve the citation UI
2. Bind every note item to source_chunk_id
3. Clicking a citation shows the source snippet
4. Improve search result ranking
5. Improve the answer prompt and citation format

Done when:

1. Key notes show page-level sources
2. Clicking citations shows source snippets
3. Search and answering demo reliably

### Phase 11: hosted demo deployment

Do:

1. Deploy the Next.js frontend
2. Deploy the Spring Boot backend
3. Deploy the Python worker
4. Deploy PostgreSQL + pgvector
5. Deploy Redis
6. Configure object storage
7. Configure environment variables and CORS
8. Test the full hosted flow

Done when:

1. The project opens from a URL
2. Upload, embeddings, search, answering, notes, editing, and export all
   work online
3. The README links the demo with run instructions

### Phase 12: Electron desktop

Do:

1. Create the Electron app
2. Reuse the web build output
3. Configure windows, menus, and app metadata
4. Connect to the cloud Backend API
5. Support local file pickers and export saving
6. Package installable macOS / Windows builds

Done when:

1. Users can install the desktop app
2. The desktop UI matches the web app
3. The desktop app completes upload/search/ask/edit/export against the
   cloud API
4. Users need no local Java, Python, PostgreSQL, or Redis

## 11. Key API Inventory

### Auth

```text
GET /auth/me
```

### Documents

```text
POST /documents
GET /documents
GET /documents/{id}
DELETE /documents/{id}
```

### Tasks

```text
POST /documents/{id}/analyze
GET /tasks/{id}
GET /documents/{id}/tasks
POST /tasks/{id}/retry
```

### Notes

```text
GET /documents/{id}/notes
GET /notes/{id}
PUT /notes/{id}
```

### Quiz

```text
GET /documents/{id}/quiz
POST /documents/{id}/quiz/generate
```

### Sources

```text
GET /documents/{id}/chunks
GET /chunks/{id}
```

### Semantic search and RAG

```text
POST /documents/{id}/search
POST /documents/{id}/ask
```

### Export

```text
POST /notes/{id}/export/markdown
POST /notes/{id}/export/pdf
```

## 12. State Transitions

### Document states

```text
UPLOADED
PROCESSING
READY
FAILED
DELETED
```

Transitions:

```text
UPLOADED -> PROCESSING -> READY
UPLOADED -> PROCESSING -> FAILED
READY -> DELETED
```

### Task states

```text
PENDING
PROCESSING
COMPLETED
FAILED
RETRYING
CANCELLED
```

Transitions:

```text
PENDING -> PROCESSING -> COMPLETED
PENDING -> PROCESSING -> FAILED
FAILED -> RETRYING -> PROCESSING
PENDING -> CANCELLED
PROCESSING -> CANCELLED
```

## 13. Error Handling

### Upload failures

Causes:

1. The file is not a PDF
2. The file is too large
3. Storage write failure

Handling:

1. The frontend shows the error
2. The backend creates no task
3. If the document was created but storage failed, mark FAILED or roll back

### Worker failures

Causes:

1. PDF download failure
2. PDF parse failure
3. LLM API timeout
4. JSON schema validation failure
5. Database write failure

Handling:

1. task.status = FAILED
2. Write error_message
3. retry_count + 1
4. Let the user retry

### Malformed AI output

Handling:

1. Enforce JSON schemas
2. Retry once automatically on validation failure
3. On repeated failure, save the raw output to a debug log
4. Mark the task FAILED

## 14. The Minimal V1 Loop

With limited time, build only this chain:

```text
PDF upload
  -> save document
  -> create task
  -> worker parse PDF
  -> chunk text
  -> generate chunk embeddings
  -> semantic search by user query
  -> worker generate notes
  -> save note
  -> editor edit note
  -> export Markdown
```

This version may defer:

1. Advanced citation UI
2. Quizzes
3. PDF export
4. OCR
5. WebSocket
6. Complex permissions

Strengthen the architectural highlights incrementally after the loop closes.

## 15. Development Checklist

At the end of each phase, verify:

1. The frontend has a page entry point
2. The API is independently testable
3. The database holds the corresponding records
4. Error states render
5. Data survives a page refresh
6. Users can only access their own data
7. The README reflects how to run the current state

## 16. Definition of Done

The project is portfolio-ready when:

1. Users can log in.
2. Users can upload PDFs.
3. The system analyzes PDFs asynchronously.
4. Users see task progress.
5. The system generates structured notes.
6. The system generates chunk embeddings.
7. Users can search PDF/note content with one sentence.
8. Users can ask the AI questions grounded in sources.
9. The system generates quizzes.
10. Notes have at least page-level citations.
11. Users can edit notes in the Tiptap editor.
12. Users can insert inline and block LaTeX.
13. Users can export Markdown.
14. The project starts locally with Docker Compose.
15. A hosted demo exists.
16. An Electron desktop wrapper can follow.
17. The README has screenshots, an architecture diagram, and technical
    documentation.
