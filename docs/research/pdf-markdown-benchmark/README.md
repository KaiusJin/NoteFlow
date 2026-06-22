# PDF Markdown Benchmark Archive

This folder keeps the durable conclusions from the PDF-to-Markdown experiments.

The raw experiment outputs were removed because they were generated artifacts:

1. Third-party tool Markdown outputs.
2. Extracted image artifacts from Docling and Marker.
3. Temporary page renders and visual crops.
4. The local experiment virtualenv.

Some archived benchmark tables still mention historical raw output paths under
`experiments/pdf_markdown/outputs/`. Those paths are retained only as provenance
inside the benchmark report; the raw files are intentionally no longer present.

The production document pipeline now stores runtime assets under:

```text
services/api/storage/uploads/
services/api/storage/rendered/
services/api/storage/regions/
```

Those runtime assets are referenced by the database and should not be moved manually.

## Archived Files

| File | Purpose |
|---|---|
| `benchmark_summary.md` | Human-readable timing and output-size benchmark summary. |
| `benchmark_summary.json` | Machine-readable benchmark summary. |
| `benchmark_scores.md` | Quality grading report for sampled Markdown outputs. |

## Experiment Conclusion

The experiments were useful for comparing Docling, Marker, Gemini page-level transcription, and NoteFlow's own pipeline.

The lasting conclusion is:

1. Do not use a single external converter as the entire pipeline.
2. Keep NoteFlow's page/region-aware visual pipeline.
3. Use VLM selectively for handwritten pages, code images, diagrams, and failed extraction pages.
4. Add math-specific cleanup before relying on exact formula QA.
5. Keep generated benchmark outputs out of the repo unless they are short summary reports.

## Current Canonical Data Locations

| Data kind | Location |
|---|---|
| Uploaded PDFs | `services/api/storage/uploads/{document_id}.pdf` |
| Rendered page images | `services/api/storage/rendered/{document_id}/page-XXX.png` |
| Cropped visual regions | `services/api/storage/regions/{document_id}/page-XXX-region-YY.png` |
| Markdown pages | PostgreSQL `document_markdown_pages` |
| Document Markdown | PostgreSQL `document_markdown_documents` |
| Chunks | PostgreSQL `document_chunks` |
| VLM results | PostgreSQL `document_vlm_results` |

## If Benchmarking Is Needed Again

Create a fresh virtual environment outside the repository or under an ignored temporary folder. Store only final benchmark summaries in this archive.
