# NoteFlow API

Spring Boot API for document upload, document metadata, and async task tracking.

## Current Endpoints

```text
GET /health
POST /documents
GET /documents
GET /documents/{id}
GET /tasks/{id}
GET /documents/{documentId}/tasks
```

## Upload Example

```bash
curl -X POST http://localhost:8080/documents \
  -F "file=@/path/to/notes.pdf" \
  -F "documentType=HANDWRITTEN_NOTES" \
  -F "title=Week 1 Notes"
```

The upload API:

1. Stores the PDF in `storage/uploads/{documentId}.pdf`.
2. Creates a `documents` row.
3. Creates a `tasks` row.
4. Enqueues a Redis task for the Python worker.

## Run Locally

Start PostgreSQL and Redis from the repo root:

```bash
docker compose up -d postgres redis
```

Then run the API:

```bash
gradle bootRun
```

Environment variables:

```env
SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:5432/noteflow
SPRING_DATASOURCE_USERNAME=noteflow
SPRING_DATASOURCE_PASSWORD=noteflow
REDIS_HOST=localhost
REDIS_PORT=6379
NOTEFLOW_UPLOAD_DIR=storage/uploads
```
