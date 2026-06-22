# NoteFlow Worker

The worker consumes document parsing tasks from Redis and updates PostgreSQL with parse results and text chunks.

## Run Locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m noteflow_worker.main
```

Required services:

1. PostgreSQL
2. Redis
3. Spring Boot API

Important environment variables:

```env
DATABASE_URL=postgresql://noteflow:noteflow@localhost:5432/noteflow
REDIS_URL=redis://localhost:6379/0
DOCUMENT_QUEUE=queue:document-analysis
```
