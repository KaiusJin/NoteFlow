# PDF Markdown And Chunk Audit Report

Date: 2026-06-22

This report audits the currently uploaded NoteFlow documents after the latest page-aware chunking and VLM retry changes.

## Executive Summary

The system is close enough to start a controlled embedding/search prototype, but it is not yet ready for a high-quality production RAG flow.

Current strengths:

1. VLM failures are now cleared for all uploaded documents.
2. `STAT230Jun17` no longer has the page 15 timeout/empty-page problem.
3. Handwritten/scanned lecture notes now chunk by page/topic instead of tiny formula fragments.
4. `CS116` pages 8, 10, and 11 have been repaired and now include the missing code screenshots.
5. Most text PDFs produce usable Markdown and citation-friendly chunk metadata.

Current risks before serious RAG quality evaluation:

1. Several CS136 slide decks still include visible page numbers, course footers, and slide headers inside chunks.
2. Math Markdown is usable for search, but formulas are often structurally messy and sometimes split into awkward Markdown/code/table fragments.
3. Some chunks are either too small because they are short self-contained slides, function-level code snippets, or too broad because topic/heading detection is still coarse.

My recommendation:

Proceed to the next step as an internal prototype: embeddings, search UI, and retrieval evaluation. Before using this as the main note QA experience, improve footer/boilerplate filtering and math Markdown cleanup.

## Dataset Overview

| Document | Pages | Type | Source type | Status |
|---|---:|---|---|---|
| Smoke Test PDF | 1 | HANDWRITTEN_NOTES | HANDWRITTEN_SCAN | Ready, but not meaningful content |
| CS136W9 | 58 | LECTURE_SLIDES | TEXT_PDF | Usable, footer noise remains |
| CS136W7 | 65 | LECTURE_SLIDES | TEXT_PDF | Usable, footer noise remains |
| CS136W10 | 39 | LECTURE_SLIDES | TEXT_PDF | Good chunk distribution, footer noise remains |
| CS116 | 17 | LECTURE_SLIDES | TEXT_PDF | Repaired; usable for prototype |
| CS136MT | 23 | LECTURE_SLIDES | MIXED | Usable, broad chunks |
| MATH138L30 | 5 | LECTURE_SLIDES | TEXT_PDF | Usable but one large chunk |
| MATH138L22 | 9 | LECTURE_SLIDES | TEXT_PDF | Usable, math formatting imperfect |
| MATH138L25 | 7 | LECTURE_SLIDES | TEXT_PDF | Usable, math formatting imperfect |
| STAT230Jun1 | 17 | COURSE_NOTES | SCANNED_PDF | Usable after page-aware chunking |
| STAT230Jun17 | 22 | HANDWRITTEN_NOTES | HANDWRITTEN_SCAN | Usable after page15 repair |

## Global Quality Metrics

### VLM

All uploaded documents currently have `0` VLM errors.

| Document | VLM regions | VLM errors |
|---|---:|---:|
| CS116 | 17 | 0 |
| CS136MT | 24 | 0 |
| CS136W10 | 20 | 0 |
| CS136W7 | 24 | 0 |
| CS136W9 | 24 | 0 |
| MATH138L22 | 4 | 0 |
| MATH138L25 | 3 | 0 |
| MATH138L30 | 2 | 0 |
| Smoke Test PDF | 1 | 0 |
| STAT230Jun1 | 17 | 0 |
| STAT230Jun17 | 22 | 0 |

### Markdown Pages

| Document | Markdown pages | Avg quality | Empty pages | Short pages | Avg chars/page |
|---|---:|---:|---:|---:|---:|
| CS116 | 17 | 0.995 | 0 | 0 | 649.5 |
| CS136MT | 23 | 0.910 | 0 | 3 | 257.9 |
| CS136W10 | 39 | 0.954 | 0 | 0 | 606.6 |
| CS136W7 | 65 | 0.970 | 0 | 0 | 491.0 |
| CS136W9 | 58 | 0.983 | 0 | 1 | 787.2 |
| MATH138L22 | 9 | 0.916 | 0 | 2 | 512.7 |
| MATH138L25 | 7 | 0.933 | 0 | 1 | 660.6 |
| MATH138L30 | 5 | 0.884 | 0 | 2 | 485.2 |
| Smoke Test PDF | 1 | 0.910 | 0 | 0 | 93.0 |
| STAT230Jun1 | 17 | 0.886 | 0 | 3 | 113.9 |
| STAT230Jun17 | 22 | 0.901 | 0 | 3 | 123.3 |

The quality score is not enough by itself. Earlier, `CS116` looked numerically good while three pages were empty. That specific issue has now been fixed, but page-level audits are still necessary.

### Chunks

| Document | Chunks | Min | Median | Max | Avg | Tiny `<80` | Oversized `>900` |
|---|---:|---:|---:|---:|---:|---:|---:|
| CS116 | 25 | 9 | 62 | 750 | 143.7 | 17 | 0 |
| CS136MT | 4 | 197 | 426.5 | 691 | 435.3 | 0 | 0 |
| CS136W10 | 9 | 95 | 715 | 771 | 659.6 | 0 | 0 |
| CS136W7 | 38 | 22 | 250.5 | 652 | 280.2 | 4 | 0 |
| CS136W9 | 62 | 18 | 182.5 | 703 | 218.4 | 8 | 0 |
| MATH138L22 | 3 | 266 | 386 | 660 | 437.3 | 0 | 0 |
| MATH138L25 | 4 | 152 | 265.5 | 712 | 348.8 | 0 | 0 |
| MATH138L30 | 1 | 669 | 669 | 669 | 669.0 | 0 | 0 |
| Smoke Test PDF | 1 | 23 | 23 | 23 | 23.0 | 1 | 0 |
| STAT230Jun1 | 3 | 96 | 197 | 261 | 184.7 | 0 | 0 |
| STAT230Jun17 | 6 | 61 | 144 | 232 | 139.5 | 2 | 0 |

Interpretation:

1. No chunk is dangerously oversized.
2. Tiny chunks are mostly short slides, titles, examples, or code snippets.
3. `CS116` tiny chunks are now mostly function-level Python snippets, which is acceptable for code search.
4. `STAT230Jun17` tiny chunks are acceptable because they correspond to short topics, not broken formulas.

## Per-Document Assessment

## CS116

Verdict: repaired and usable for prototype.

Previous problem:

Pages 8, 10, and 11 were marked as empty Markdown even though they contained important Python code screenshots.

Repair result:

1. Page 8: `func5` and `func6`.
2. Page 10: `func9` and `func10`.
3. Page 11: `func11` and `func12`.

These functions are now present in Markdown and chunks. CS116 now has:

```text
empty Markdown pages: 0
VLM errors: 0
VLM regions: 17
chunks: 25
```

Root cause:

The perceptual-hash repeated-image filter was too aggressive for black-background code screenshots. Multiple code images had similar hashes and were incorrectly treated like decorative repeated images. The region builder now preserves code-like/high-information regions and adds a page-level fallback when a visually meaningful page would otherwise have no regions.

Remaining caveat:

CS116 now has many small chunks because each code function is chunked separately. That is acceptable for code search, since users are likely to ask about individual functions.

## CS136W7

Verdict: usable for prototype, needs cleanup.

Good:

The deck has 65 Markdown pages and no empty pages. Most chunks are within a good range.

Problems:

1. Footers and visible page labels remain in content, such as `1/49 CS 136 - Winter 2026 Section 7`.
2. Some code chunks are very small but complete.
3. Some slide code blocks are represented as multiple separate fenced snippets.

Impact:

Search should work, but retrieval may include footer noise and less clean citations.

Recommended fix:

Improve repeated footer/header filtering using page position and normalized repeated text families.

## CS136W9

Verdict: usable for prototype, needs cleanup.

Good:

The deck has strong coverage and no empty pages. Visual diagrams are captured, including linked-list diagrams.

Problems:

1. Several short chunks are caused by one-slide concepts.
2. Footers and section labels are still present.
3. Some content from diagrams and tables is flattened awkwardly.

Impact:

Good enough for semantic search experiments. Less ideal for polished AI note generation.

Recommended fix:

Apply the same footer cleanup as CS136W7. Consider diagram-specific Markdown blocks with captions and structured metadata.

## CS136W10

Verdict: good for prototype.

Good:

Chunk distribution is healthy:

```text
9 chunks
median 715 tokens
min 95
max 771
0 tiny chunks
```

Problems:

1. Chunks are broad, often 4 to 6 pages.
2. Footers remain.

Impact:

Good recall, slightly weaker precision. This is acceptable for first embedding/search work.

Recommended fix:

Use heading-aware boundaries more aggressively for large decks if retrieval returns too much context.

## CS136MT

Verdict: usable for prototype.

Good:

No VLM errors, no empty pages, no tiny chunks. Chunks are reasonably sized.

Problems:

1. Only 4 chunks for 23 pages, so chunks are broad.
2. Some page boundaries merge multiple review topics.

Impact:

Good for broad QA. Less precise for pinpoint citations.

Recommended fix:

Later, split by visible section headings such as short answer, memory snapshots, style marks, and practice problems.

## MATH138L22

Verdict: usable with caution.

Good:

No empty pages, no tiny chunks. Chunk sizes are acceptable.

Problems:

1. Math formulas are imperfectly reconstructed.
2. Some formula fragments appear as awkward text tokens.
3. Lecture title pages are very short.

Impact:

Semantic search by topic should work. Exact formula QA will be weaker.

Recommended fix:

Use a math-aware Markdown normalizer or VLM fallback for dense formula regions.

## MATH138L25

Verdict: usable with caution.

Good:

No empty pages and no tiny chunks. Chunks are generally coherent.

Problems:

1. Some math expressions are malformed in Markdown.
2. Some formulas appear inside code fences or table-like structures.
3. A large chunk spans pages 4 to 6 and mixes a long ratio-test solution with examples.

Impact:

Fine for topic search and rough RAG. Not yet reliable for exact symbolic math answers.

Recommended fix:

Add formula block cleanup and a math-specific page/section chunker.

## MATH138L30

Verdict: usable but too coarse.

Good:

The whole document becomes one 669-token chunk, which is not too large.

Problems:

One chunk for 5 pages reduces citation precision.

Impact:

Acceptable for retrieval because the document is short. Later, split by theorem/example/proof.

## STAT230Jun1

Verdict: usable after page-aware chunking.

Good:

The scanned notes are now chunked into 3 coherent topic groups:

1. Gambler's Ruin.
2. Branching Process / Prosecutor's Fallacy.
3. Simpson's Paradox.

Problems:

Handwriting transcription has mistakes such as names/words being misread. Some diagrams are described only textually.

Impact:

Good enough for semantic search. Detailed numerical/table reconstruction may be imperfect.

## STAT230Jun17

Verdict: usable after repair.

Good:

The previous page 15 timeout is fixed. The document now has:

```text
6 chunks
0 VLM errors
0 empty Markdown pages
```

Chunk boundaries are now sensible:

1. Pages 1-7: Geometric distribution / PMF / CDF.
2. Page 8: die example.
3. Pages 9-13: expectation / memorylessness / proof.
4. Pages 14-17: Happy Meal problem.
5. Pages 18-19: Negative Binomial.
6. Pages 20-22: Banach / branching probability.

Problems:

Some handwritten words are still uncertain or mistranscribed, for example `[unclear]` and imperfect toy/coupon wording.

Impact:

This is now good enough for the next prototype step.

## Smoke Test PDF

Verdict: not meaningful.

It contains a blank image region and only produces:

```text
The image region is completely blank, containing no visible text, diagrams, or other content.
```

Do not use this document to evaluate real retrieval quality.

## Main Findings

## 1. Markdown Quality Still Needs Guardrails

Chunking is now better than before, especially for handwritten notes. The main remaining risk is still upstream Markdown quality: if visual/code content is missed before chunking starts, retrieval cannot recover it.

The `CS116` pages 8, 10, and 11 issue has been repaired, and it should remain a regression test for future parser changes.

## 2. VLM Retry Is Now Healthy

All current documents have `0` VLM errors. The `STAT230Jun17` page 15 timeout was repaired and should not recur silently because scanned/handwritten page-level VLM failures now fail the task after retries.

## 3. Chunk Sizes Are Mostly Safe

There are no chunks above 900 estimated tokens. This means retrieval context should not be too large for the next prototype.

The remaining tiny chunks are mixed:

1. Acceptable: short standalone slides, short examples, small handwritten topics.
2. Acceptable: function-level code snippets in CS116.
3. Not acceptable: tiny chunks caused by future missing pages or split titles.

## 4. Footer/Header Noise Needs Cleanup

CS136 decks still include page labels such as:

```text
1/49 CS 136 - Winter 2026 Section 7
41/41 CS 136 - Winter 2026 Section 10
```

These should be removed from Markdown/chunks before serious embedding quality evaluation.

## 5. Math Markdown Needs A Specialized Pass

MATH138 documents are usable for topic search but not reliable enough for exact formula QA. Formula extraction often preserves meaning roughly, but structure is messy.

## Recommended Next Steps

## P0: Keep Visual Fallback For Empty Markdown Pages

This has been implemented for the CS116 failure mode. Keep it as a required guardrail:

1. After Markdown page generation, detect pages with `empty_markdown_page` or very low text length.
2. Check the page asset metrics: `image_coverage`, `image_count`, `drawing_count`, and `text_length`.
3. If the page is visually meaningful, create a full-page visual region.
4. Run VLM on that page.
5. Rebuild Markdown and chunks.

This fixed `CS116` pages 8, 10, and 11.

## P1: Improve Footer And Boilerplate Removal

Use repeated normalized strings plus page-position metadata to remove course footers, slide numbers, copyright notices, and repeated section labels.

Do not use a naive regex only, because lecture notes can contain code, formulas, and page-like strings.

## P1: Add Chunk Quality Flags

Store chunk quality metadata:

1. `tinyChunk`
2. `largePageSpan`
3. `containsEmptyPageMarker`
4. `containsFooterNoise`
5. `hasVlmError`
6. `lowMarkdownQuality`

Expose these in the frontend so we can inspect bad chunks quickly.

## P2: Math-Specific Markdown Cleanup

For math-heavy PDFs:

1. Normalize formula blocks.
2. Avoid putting math into `text` code fences.
3. Preserve theorem/example/proof boundaries.
4. Prefer page/section chunks over token-only chunks.

## Can We Move To The Next Step?

Yes, but only as a prototype.

I would proceed with:

1. Embedding schema and embedding generation.
2. Search endpoint.
3. Frontend search UI.
4. Retrieval inspection page that shows chunk text, page images, and quality flags.

I would not yet claim the document pipeline is production-ready. Before relying on AI answers, improve footer cleanup and math Markdown quality.

The practical path is:

1. Start embeddings.
2. Build search and retrieval inspection.
3. Improve footer/boilerplate cleanup.
4. Use retrieval results to drive the next parser improvements.
