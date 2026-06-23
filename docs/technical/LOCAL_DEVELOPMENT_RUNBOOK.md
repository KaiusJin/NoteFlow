# Local Development Runbook

This document explains how to open the NoteFlow web app locally.

## 1. Services

The local app needs four running pieces:

```text
PostgreSQL + pgvector
Redis
Spring Boot API
Python worker
Static web frontend
```

The frontend calls the API at:

```text
http://localhost:8080
```

## 2. Start Infrastructure

From the repo root:

```bash
docker-compose up -d postgres redis
```

Check:

```bash
docker-compose ps
```

Expected services:

```text
noteflow-postgres
noteflow-redis
```

## 3. Start API

From the repo root:

```bash
gradle -p services/api bootRun
```

API URL:

```text
http://localhost:8080
```

Health check:

```bash
curl http://localhost:8080/health
```

Expected response:

```json
{"status":"ok"}
```

## 4. Start Worker

From the repo root:

```bash
set -a
source .env
set +a
PYTHONPATH=services/worker services/worker/.venv/bin/python -m noteflow_worker.main
```

The worker consumes Redis parse tasks. Without the worker, upload may succeed but parsing will not complete.

## 5. Start Frontend

From `apps/web`:

```bash
python3 -m http.server 3000
```

Frontend URL:

```text
http://localhost:3000
```

If port `3000` is busy, use another port:

```bash
python3 -m http.server 3001 --bind 127.0.0.1
```

Then open:

```text
http://127.0.0.1:3001
```

The API CORS config allows local development origins:

```text
http://localhost:*
http://127.0.0.1:*
```

## 6. Frontend API URL Override

The frontend defaults to:

```text
http://localhost:8080
```

To override it in the browser console:

```js
localStorage.setItem("noteflowApiBaseUrl", "http://localhost:8080");
location.reload();
```

To clear the override:

```js
localStorage.removeItem("noteflowApiBaseUrl");
location.reload();
```

## 7. Troubleshooting `Failed to fetch`

`Failed to fetch` usually means the browser could not call the API.

Check:

1. API is running:

```bash
curl http://localhost:8080/health
```

2. Frontend origin is local:

```text
http://localhost:<port>
http://127.0.0.1:<port>
```

3. CORS allows the frontend port.

4. The browser is not using an old API override:

```js
localStorage.getItem("noteflowApiBaseUrl");
```

5. Docker infrastructure is running:

```bash
docker-compose ps
```

## 8. Current Local URLs

Preferred:

```text
Frontend: http://localhost:3000
API:      http://localhost:8080
```

Fallback when port `3000` is occupied:

```text
Frontend: http://127.0.0.1:3001
API:      http://localhost:8080
```
