# NoteFlow Worker

The worker consumes document parsing tasks from Redis and updates PostgreSQL with parse results and text chunks.

## Run Locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m noteflow_worker.main
```

For an NVIDIA GPU OCR worker, additionally install the CUDA-matched
`paddlepaddle-gpu` wheel described by `requirements-gpu.txt`.
For Apple Silicon MPS OCR, use a PyTorch-supported Python runtime and install
`requirements-mps.txt`; `PDF_OCR_BACKEND=auto` will select EasyOCR on MPS.

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

Copy `.env.example` for the complete CPU/GPU, OCR, VLM router, multi-key,
MCP Streamable HTTP, retry, and cleanup configuration.

Pool sizing is intentionally separated:

- document pool: independent uploaded documents;
- PDF render pool: conservative 1–2 MuPDF workers by default;
- GPU/CPU OCR pool: GPU workers derived from free VRAM and model footprint;
- VLM pool: provider-rate-limit-bound concurrent micro-batches.

Task admission also uses three Redis priority lists. Interactive work is priority
0, parsing/notes are priority 1, and embeddings are priority 2. Weighted dequeue
prevents starvation, while `WORKER_MAX_BACKGROUND_TASKS` reserves capacity for
user-visible work.

Benchmark the deployment host before overriding the render pool:

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark_pdf_pools.py --pages 48 --workers 1,2,4,8
```
