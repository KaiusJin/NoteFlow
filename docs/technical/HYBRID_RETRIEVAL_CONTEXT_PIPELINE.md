# Hybrid Retrieval And Context Construction Pipeline

This document defines the implementation after NoteFlow's first embedding-search
stage. It covers candidate recall, lexical retrieval, fusion, reranking,
deduplication, context construction, citations, failure handling, interfaces,
and quality evaluation.

It does not define final answer generation. RAG answer generation and LangGraph
orchestration consume the output of this pipeline in a later phase.

## 1. Current State And Decision

The current search implementation performs:

```text
query
  -> Gemini query embedding
  -> pgvector cosine similarity
  -> metadata and document-scope filters
  -> Top K PDF chunks and/or AI Note sections
```

Current source domains:

```text
PDF     -> DOCUMENT_CHUNK
AI_NOTE -> AI_NOTE_SECTION
```

The existing quality suite shows strong initial document-level recall:

```text
Recall@5 = 1.000
MRR      = 1.000
```

This means embedding search is a useful semantic recall channel. It is not yet
the complete retrieval layer because:

1. Exact mathematical notation, theorem numbers, identifiers, and code symbols
   are not always well represented by semantic embeddings.
2. A global vector Top K can include weakly related results.
3. PDF chunks and AI Note sections may describe the same source material.
4. Adjacent chunks may need to be reassembled before they are useful to an LLM.
5. Raw similarity scores from different retrieval methods are not directly
   comparable.
6. Search results currently have no evidence-sufficiency decision.

The next implementation is therefore:

```text
Hybrid Retrieval
  + Reciprocal Rank Fusion
  + optional Reranking
  + Context Builder
```

Current implementation status:

```text
Vector Top-30 candidate recall: implemented
PostgreSQL full-text recall with generated tsvector and GIN: implemented
Exact theorem/code/formula recall with normalized trigram index: implemented
Three-channel bounded concurrent execution: implemented
Weighted Reciprocal Rank Fusion: implemented
Per-channel timeout and degradation: implemented
Quality filtering: implemented
Near-duplicate removal: implemented
Query-aware deterministic reranking: implemented
Soft PDF evidence retention: implemented
Adjacent PDF chunk expansion: implemented
Token-bounded context construction: implemented
Stable request-local citations: implemented
Deterministic evidence status: implemented
External Gemini reranker adapter with structured output and fallback: implemented
Local cross-encoder reranker: reserved provider
```

## 2. Goals And Non-Goals

### 2.1 Goals

1. Preserve the existing `PDF`, `AI_NOTE`, `MIXED`, and `CUSTOM` scope rules.
2. Recall candidates using both semantic meaning and lexical evidence.
3. Support mathematics, theorem labels, code identifiers, and phrases.
4. Keep ranking deterministic before the optional reranker.
5. Prevent results from crossing user ownership or selected-document boundaries.
6. Remove duplicate and near-duplicate evidence.
7. Combine adjacent PDF chunks only when they are structurally compatible.
8. Build a token-bounded context package with stable citations.
9. Detect when retrieved evidence is insufficient.
10. Keep retrieval independent from the final LLM and from LangGraph.

### 2.2 Non-Goals

This phase will not:

1. Generate a natural-language answer.
2. Add an autonomous agent loop.
3. Replace the PDF-to-Markdown or chunking pipeline.
4. Introduce Elasticsearch, OpenSearch, or another retrieval cluster initially.
5. Use an LLM as the only ranking mechanism.
6. Treat an AI Note as stronger evidence than its source PDF by default.

## 3. End-To-End Flow

```text
SearchRequest
  -> Request validation
  -> Ownership and source-scope resolution
  -> Query analysis
  -> Parallel candidate recall
       -> vector recall
       -> lexical recall
       -> exact-signal recall
  -> candidate normalization
  -> Reciprocal Rank Fusion (RRF)
  -> source-aware deduplication
  -> optional reranking
  -> adjacent PDF chunk expansion/merge
  -> context budget selection
  -> evidence sufficiency check
  -> RetrievalResponse
```

The execution remains a normal Spring Boot service workflow. LangGraph is not
needed for this deterministic single-pass pipeline.

LangGraph becomes useful later when the system can branch:

```text
insufficient evidence
  -> rewrite query
  -> retrieve again
  -> ask user for clarification
  -> or generate answer
```

## 4. Module Architecture

Recommended Java package:

```text
com.noteflow.retrieval
```

Recommended components:

| Component | Responsibility |
|---|---|
| `RetrievalService` | Orchestrates one retrieval request. |
| `RetrievalScopeResolver` | Enforces ownership, readiness, mode, and selected files. |
| `QueryAnalyzer` | Produces normalized query and exact-search signals. |
| `VectorCandidateRetriever` | Retrieves semantic candidates from pgvector. |
| `LexicalCandidateRetriever` | Retrieves PostgreSQL full-text candidates. |
| `ExactSignalRetriever` | Boosts theorem labels, formulas, quoted phrases, and code identifiers. |
| `ReciprocalRankFusion` | Fuses ranked lists without comparing raw score scales. |
| `CandidateDeduplicator` | Removes duplicate source objects and near-duplicate content. |
| `Reranker` | Optional provider interface for ranking a small candidate set. |
| `ContextBuilder` | Expands neighbors, merges compatible evidence, and applies token budget. |
| `EvidenceEvaluator` | Determines whether evidence is strong and diverse enough. |
| `CitationFactory` | Creates stable source references used by the future answer layer. |

The existing `SearchService` should not accumulate all these responsibilities.
During migration it can delegate to `RetrievalService` while preserving existing
endpoints.

## 5. Query Analysis

Input:

```json
{
  "query": "Explain Theorem 4.4.10 and the Taylor error bound",
  "mode": "MIXED",
  "pdfDocumentIds": [],
  "aiNoteDocumentIds": []
}
```

Output:

```json
{
  "originalQuery": "Explain Theorem 4.4.10 and the Taylor error bound",
  "normalizedQuery": "explain theorem 4.4.10 and the taylor error bound",
  "quotedPhrases": [],
  "identifiers": ["4.4.10"],
  "formulaTokens": [],
  "codeIdentifiers": [],
  "language": "en",
  "requiresExactRecall": true
}
```

### 5.1 Normalization Rules

Normalization may:

1. Trim and collapse whitespace.
2. Apply Unicode normalization.
3. Normalize visually equivalent punctuation.
4. Preserve case-sensitive code identifiers in a separate field.
5. Preserve theorem numbers, page references, operators, and formula tokens.
6. Preserve both original and normalized forms.

Normalization must not:

1. Remove mathematical operators.
2. Flatten all formulas into ordinary words.
3. Delete underscores from code identifiers.
4. interpret PDF page counters as user query syntax without evidence.
5. Use a broad regular expression to rewrite document content.

### 5.2 Exact Signals

Exact-signal recall is activated when the query contains signals such as:

```text
Theorem 4.4.10
CS136
func12
list_cp_bad
E[X^2]
O(n log n)
"prosecutor's fallacy"
```

These signals are supplemental. They do not bypass ownership or source filters.

## 6. Candidate Recall

### 6.1 Candidate Counts

Defaults:

```text
vectorCandidateLimit  = 30
lexicalCandidateLimit = 30
exactCandidateLimit   = 15
fusionCandidateLimit  = 30
rerankCandidateLimit  = 12
finalResultLimit      = 8
```

These values are configuration, not public API guarantees.

The initial recall size must be larger than the final result size. Retrieving
only the final Top 5 before fusion prevents another retrieval channel from
recovering missed evidence.

### 6.2 Vector Recall

Vector recall reuses the current embedding workflow:

```sql
ORDER BY embedding <=> :query_embedding
LIMIT :vector_candidate_limit
```

Required filters:

```text
current user ownership
document status = READY
embedding provider
embedding model
source domain
selected document IDs
latest READY AI Note version
```

Returned candidate fields:

```text
source domain
source object type
source object ID
document ID
page range
title
content
metadata
vector rank
vector similarity
```

### 6.3 Lexical Recall

The first implementation uses PostgreSQL full-text search to avoid introducing
another infrastructure service.

This is a lexical ranking channel, not a claim that PostgreSQL `ts_rank_cd` is
the exact BM25 algorithm.

Recommended generated column:

```sql
ALTER TABLE document_embeddings
ADD COLUMN search_vector tsvector
GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', COALESCE(metadata_json::jsonb ->> 'title', '')), 'A') ||
  setweight(to_tsvector('simple', COALESCE(embedding_text, '')), 'B')
) STORED;

CREATE INDEX idx_document_embeddings_search_vector
ON document_embeddings
USING GIN (search_vector);
```

Candidate query:

```sql
SELECT
  ...,
  ts_rank_cd(search_vector, websearch_to_tsquery('simple', :query)) AS lexical_score
FROM document_embeddings
WHERE search_vector @@ websearch_to_tsquery('simple', :query)
  AND ...
ORDER BY lexical_score DESC
LIMIT :lexical_candidate_limit;
```

Use the `simple` text-search configuration initially because NoteFlow contains:

1. English technical vocabulary.
2. Code and identifiers.
3. Mathematical labels.
4. Mixed-language queries.

Language-specific stemming can be evaluated later. It must not replace the
original searchable text.

### 6.4 Exact-Signal Recall

PostgreSQL FTS tokenization may still lose punctuation-heavy expressions.
Exact-signal recall therefore queries normalized auxiliary fields or uses
escaped `ILIKE` against a bounded, already scoped set.

Examples:

```text
title exact match
theorem number match
code identifier match
formula token match
quoted phrase match
```

Rules:

1. Always parameterize SQL.
2. Escape wildcard characters for literal matching.
3. Run exact matching only after ownership and document scope are resolved.
4. Do not scan every user's full corpus.
5. Cap exact candidates independently.

For a larger production corpus, replace this channel with a true BM25 engine or
a specialized PostgreSQL extension only after benchmark evidence justifies the
operational cost.

## 7. Reciprocal Rank Fusion

Raw cosine similarity, `ts_rank_cd`, and exact-match scores have different
scales. They must not be added directly.

RRF score:

```text
RRF(candidate) = sum(weight(channel) / (k + rank(channel)))
```

Default:

```text
k = 60
vector weight  = 1.0
lexical weight = 1.0
exact weight   = 1.2
```

Example:

```text
candidate A:
  vector rank  = 1
  lexical rank = 4

RRF(A) = 1/(60+1) + 1/(60+4)
```

Rules:

1. Candidate identity is `(sourceDomain, sourceObjectType, sourceObjectId)`.
2. A candidate appearing in multiple channels receives contributions from each.
3. Missing channels contribute zero.
4. Keep channel ranks and scores for diagnostics.
5. The exact channel receives only a modest boost. An exact token alone must not
   override strongly contradictory semantic evidence.

## 8. Source-Aware Deduplication

Deduplication occurs twice:

```text
after fusion
after optional reranking
```

### 8.1 Exact Duplicate

Keep one candidate when source identity is identical.

### 8.2 Content Duplicate

Use normalized content hash first. If hashes differ, use a low-cost overlap
metric such as token shingles or Jaccard similarity.

Suggested threshold:

```text
nearDuplicateThreshold = 0.88
```

### 8.3 PDF And AI Note Overlap

PDF and AI Note results are not automatically duplicates:

```text
PDF     = primary source evidence
AI Note = organized explanatory layer
```

When both cover the same pages and concepts:

1. Retain the PDF candidate as primary citation evidence.
2. Retain the AI Note only if it contributes a clearer explanation, definition,
   derivation, pitfall, or example.
3. Link them using `relatedCandidateIds`.
4. Do not spend the context budget on repeated wording.

### 8.4 Source Diversity

Do not impose a fixed 50/50 PDF and AI Note quota.

Use soft diversity:

1. Prefer at least one PDF result when PDF evidence exists.
2. Allow AI Note-heavy results for explanation-oriented queries.
3. Allow PDF-heavy results for exact quotations, code, formulas, and page-level
   questions.
4. Never inject a weak source merely to satisfy diversity.

## 9. Optional Reranking

Reranking is applied only to the fused Top N candidates.

Interface:

```java
public interface Reranker {
    String providerName();
    List<RerankedCandidate> rerank(
        String query,
        List<RetrievalCandidate> candidates,
        int limit
    );
}
```

Implementations:

```text
DisabledReranker
GeminiReranker
OpenAIReranker        reserved
LocalCrossEncoder     reserved
```

Configuration:

```text
RETRIEVAL_RERANKER_PROVIDER=disabled
GEMINI_RERANK_MODEL=gemini-2.5-flash
RETRIEVAL_EXTERNAL_RERANKER_TIMEOUT_SECONDS=20
RETRIEVAL_EXTERNAL_RERANKER_CANDIDATE_LIMIT=12
```

Set `RETRIEVAL_RERANKER_PROVIDER=gemini` to enable the external reranker.
`GEMINI_API_KEY` is shared with the existing Gemini provider configuration.

Initial recommendation:

1. Ship deterministic RRF first.
2. Add `GeminiReranker` behind configuration.
3. Evaluate it against the regression set before enabling it by default.
4. Later evaluate a local cross-encoder for predictable cost and latency.

Reranker input should contain:

```text
query
candidate ID
source label
title
page range
bounded content excerpt
```

Reranker output must be structured:

```json
{
  "candidateId": "candidate-3",
  "relevance": 0.91,
  "reason": "Directly defines the requested theorem."
}
```

The provider must not be allowed to create new evidence or modify citations.

Failure behavior:

```text
timeout / invalid JSON / provider disabled
  -> retain deterministic RRF order
  -> record reranker status
  -> do not fail the entire retrieval request
```

## 10. Adjacent Chunk Expansion And Merge

Reranking identifies useful evidence units. The Context Builder may then load
neighboring PDF chunks.

Neighbor expansion is allowed only when:

1. Candidates belong to the same document.
2. Chunk indices are adjacent.
3. Page ranges are adjacent or overlapping.
4. Heading lineage is equal or structurally compatible.
5. Merging does not cross an obvious theorem/example/solution boundary.
6. Merging stays within the per-evidence token limit.

Recommended behavior:

```text
selected chunk
  -> inspect previous and next chunk
  -> add neighbor only when continuity evidence exists
  -> preserve original chunk IDs and page ranges
```

Do not automatically merge an entire page. Page boundaries are metadata, not
semantic boundaries.

For AI Note sections:

1. Do not merge the entire generated note.
2. Optionally attach one parent heading or one adjacent section.
3. Preserve section type such as `THEOREM`, `EXAMPLE`, `PITFALL`, or
   `CODE_EXPLANATION`.

## 11. Context Builder

### 11.1 Input

```text
ranked candidates
query analysis
source scope
token budget
```

### 11.2 Output

```json
{
  "query": "How does Taylor's inequality bound the remainder?",
  "evidenceStatus": "SUFFICIENT",
  "contextTokenCount": 3120,
  "items": [
    {
      "citationId": "S1",
      "sourceDomain": "PDF",
      "documentId": "uuid",
      "documentTitle": "MATH138L30",
      "pageStart": 1,
      "pageEnd": 4,
      "sourceObjectIds": ["uuid", "uuid"],
      "title": "Taylor's Inequality",
      "content": "...",
      "retrievalScore": 0.0321,
      "rerankScore": 0.94
    }
  ]
}
```

### 11.3 Token Budget

Initial defaults:

```text
totalContextTokens    = 6000
maxEvidenceItems      = 8
maxTokensPerItem      = 1400
reservedAnswerTokens  = configured by answer model
```

Selection order:

1. Highest final relevance.
2. Primary PDF evidence.
3. Non-duplicate explanatory AI Note evidence.
4. Additional evidence that increases concept or page coverage.

Every truncated item must retain:

```text
title
source
page range
original source object IDs
truncation flag
```

### 11.4 Citation Rules

Stable request-local citation IDs:

```text
S1, S2, S3...
```

PDF citation:

```text
[S1] MATH138L30, PDF pages 1-4
```

AI Note citation:

```text
[S2] MATH138L30, AI Note: Taylor's Inequality
```

If an AI Note section contains source page metadata, keep it, but still label it
as AI Note. Do not present it as direct PDF evidence.

## 12. Evidence Sufficiency

Initial implementation should be deterministic.

Possible statuses:

```text
SUFFICIENT
WEAK
INSUFFICIENT
NO_RESULTS
```

Signals:

1. Number of results above a minimum vector or reranker threshold.
2. Whether the Top 1 and Top 3 agree on the topic.
3. Whether exact requested terms are present when required.
4. Whether at least one primary PDF source exists.
5. Whether evidence comes from the user-selected scope.
6. Whether results are dominated by blank, boilerplate, or low-information text.

Example first policy:

```text
NO_RESULTS:
  no candidates

INSUFFICIENT:
  no relevant candidate survives filtering

WEAK:
  only one weak candidate, or only AI Note evidence for a source-specific claim

SUFFICIENT:
  at least one strong primary candidate and supporting content
```

Thresholds must be calibrated from the quality suite. They must not be invented
from a single query.

## 13. API Design

### 13.1 Preserve Search API

Existing endpoint:

```http
POST /search
```

It may continue to return simple search cards for the frontend.

Implemented context retrieval endpoint:

```http
POST /retrieval
```

Request:

```json
{
  "query": "How does Taylor's inequality bound the remainder?",
  "mode": "MIXED",
  "topK": 8,
  "pdfDocumentIds": [],
  "aiNoteDocumentIds": [],
  "maxContextTokens": 6000
}
```

This endpoint currently runs vector candidate recall, quality filtering,
deduplication, deterministic reranking, PDF evidence retention, adjacent chunk
expansion, context budgeting, citation creation, and evidence evaluation.

### 13.2 Add Retrieval Debug API

Development endpoint:

```http
POST /retrieval/debug
```

Request:

```json
{
  "query": "Why is node sharing unsafe in C?",
  "mode": "MIXED",
  "topK": 8,
  "pdfDocumentIds": [],
  "aiNoteDocumentIds": [],
  "options": {
    "vectorCandidates": 30,
    "lexicalCandidates": 30,
    "rerank": false,
    "includeDiagnostics": true
  }
}
```

Response:

```json
{
  "query": "Why is node sharing unsafe in C?",
  "mode": "MIXED",
  "evidenceStatus": "SUFFICIENT",
  "results": [],
  "diagnostics": {
    "vectorCount": 30,
    "lexicalCount": 18,
    "exactCount": 0,
    "fusedCount": 30,
    "rerankerProvider": "disabled",
    "elapsedMs": {
      "embedding": 180,
      "vectorRecall": 14,
      "lexicalRecall": 8,
      "fusion": 1,
      "contextBuild": 3,
      "total": 209
    }
  }
}
```

Production responses should not expose prompts, provider secrets, raw model
responses, or another user's source metadata.

### 13.3 Internal Interface For RAG

```java
RetrievalContext retrieveForAnswer(RetrievalRequest request);
```

This is the contract consumed by the future answer workflow and LangGraph nodes.
The answer layer must not directly query `document_embeddings`.

## 14. Database Changes

Required first migration:

```text
document_embeddings.search_vector
GIN index on search_vector
supporting B-tree indexes for scope filters
```

Recommended indexes:

```sql
CREATE INDEX idx_document_embeddings_document_domain
ON document_embeddings(document_id, source_domain);

CREATE INDEX idx_document_embeddings_provider_model
ON document_embeddings(embedding_provider, embedding_model);
```

Metadata currently stored as text should eventually become:

```sql
metadata_json JSONB
```

This enables safer indexing and filtering. Convert it in a dedicated migration,
not through an implicit cast on every request.

No separate retrieval-result table is required initially. Add request tracing
later only if evaluation and observability need historical runs.

## 15. Concurrency, Timeout, And Degradation

Vector and lexical recall are independent and should run concurrently using a
bounded executor.

Suggested budgets:

```text
query embedding timeout = 15 seconds
database recall timeout = 5 seconds per channel
reranker timeout        = 20 seconds
total retrieval timeout = 30 seconds without reranker
```

Degradation:

| Failure | Behavior |
|---|---|
| Query embedding fails | Run lexical + exact recall and mark semantic channel unavailable. |
| Lexical recall fails | Continue with vector recall. |
| Exact recall fails | Continue with vector + lexical recall. |
| Reranker fails | Use RRF order. |
| Neighbor loading fails | Return unexpanded candidates. |
| All channels fail | Return explicit retrieval error; do not fabricate evidence. |

## 16. Caching

Safe first caches:

1. Query embedding cache keyed by provider, model, and normalized-query hash.
2. Short-lived retrieval cache keyed by user, scope, query hash, retrieval
   configuration, and embedding corpus version.

Do not cache only by query text. The same query can have different permitted
documents and source selections.

Corpus version must change when:

```text
chunks change
AI Note version changes
embeddings change
document access changes
```

## 17. Security And Data Isolation

Scope resolution happens before every retrieval channel.

Required rules:

1. Every selected document belongs to the current user.
2. Only `READY` documents are searchable.
3. AI Note retrieval uses the latest `READY` note version.
4. `CUSTOM` mode searches only explicitly selected source/domain pairs.
5. SQL is parameterized.
6. Cached results include user and scope identity.
7. Reranker input contains only already authorized evidence.
8. Logs exclude full source text unless debug logging is explicitly enabled
   locally.

## 18. Testing And Evaluation

### 18.1 Unit Tests

Test:

1. Scope resolution.
2. Query normalization without destroying formulas or code.
3. Vector/lexical candidate mapping.
4. RRF score and tie behavior.
5. Duplicate removal.
6. PDF/AI Note overlap policy.
7. Neighbor merge boundaries.
8. Token budget enforcement.
9. Citation stability.
10. Every degradation path.

### 18.2 Database Integration Tests

Use PostgreSQL with pgvector and FTS to verify:

1. Vector scope filters.
2. GIN lexical search.
3. Mixed-domain retrieval.
4. Custom file isolation.
5. Latest AI Note version filtering.
6. Mathematical and code queries.

### 18.3 Retrieval Quality Dataset

Extend:

```text
services/api/src/test/resources/search-quality-cases.json
```

Each case should eventually label:

```json
{
  "query": "...",
  "relevantSourceObjectIds": ["uuid"],
  "acceptableDocumentIds": ["uuid"],
  "requiredConcepts": ["..."],
  "forbiddenDocumentIds": ["uuid"],
  "expectedSourceDomains": ["PDF", "AI_NOTE"]
}
```

Document-level relevance is sufficient for early testing but not for final RAG
evaluation. The next dataset version should label relevant chunk/section IDs.

### 18.4 Metrics

Required:

```text
Recall@10
Recall@30
MRR
nDCG@10
Precision@5
custom-scope violation count
empty/boilerplate result rate
mean target evidence share
p50/p95 retrieval latency
reranker fallback rate
```

Initial quality gates:

```text
custom-scope violations = 0
Recall@10 >= current semantic baseline
MRR >= current semantic baseline - agreed tolerance
Precision@5 improves over vector-only baseline
p95 without reranker < 2 seconds after query embedding is cached
```

The existing corpus is small, so latency gates must be re-evaluated with a
larger document set.

## 19. Observability

Record per request:

```text
request ID
user ID or non-sensitive hash
mode and selected-document count
query hash
channel candidate counts
fusion count
reranker status
evidence status
latency by stage
final source-domain distribution
```

Do not record API keys, full prompts, or full private documents in normal logs.

## 20. Implementation Sequence

### Phase 1: Retrieval Contracts

Status: implemented.

1. Add retrieval request, candidate, diagnostics, context, and citation models.
2. Extract current vector SQL into `VectorCandidateRetriever`.
3. Extract scope logic into `RetrievalScopeResolver`.
4. Preserve current `/search` behavior through an adapter.

### Phase 2: Lexical Recall

Status: implemented using PostgreSQL generated `tsvector`,
`websearch_to_tsquery`, `ts_rank_cd`, and a GIN index.

1. Add `search_vector` migration and GIN index.
2. Implement `LexicalCandidateRetriever`.
3. Implement exact-signal extraction and bounded exact recall.
4. Add database integration tests.

### Phase 3: Fusion And Deduplication

Status: implemented. Vector, lexical, and exact-signal ranked lists are fused
using weighted RRF before deduplication and deterministic query-aware reranking.

1. Implement deterministic weighted RRF.
2. Add candidate diagnostics.
3. Add exact and near-duplicate removal.
4. Benchmark vector-only versus hybrid retrieval.

### Phase 4: Context Builder

Status: implemented for vector retrieval.

1. Implement neighbor loading.
2. Add structural merge rules.
3. Add token budget selection.
4. Add citation generation.
5. Add deterministic evidence sufficiency.

### Phase 5: Optional Reranker

Status: the deterministic in-process reranker and an optional Gemini structured
reranker are implemented. Gemini is disabled by default and falls back to the
deterministic order on timeout, quota failure, invalid JSON, or missing
candidate IDs. A local cross-encoder remains a reserved provider.

1. Add provider interface and disabled implementation.
2. Implement Gemini structured-output reranker.
3. Add timeout and RRF fallback.
4. Enable only if offline evaluation improves Precision@5/nDCG without
   unacceptable latency.

### Phase 6: RAG And LangGraph

After retrieval quality is accepted:

```text
classify query
  -> retrieve
  -> evaluate evidence
  -> if weak: rewrite and retrieve again
  -> if ambiguous: request clarification
  -> if sufficient: generate cited answer
  -> verify citations
```

LangGraph should orchestrate this conditional workflow. It should call
`RetrievalService`; it should not duplicate retrieval SQL or ranking logic.

## 21. Acceptance Criteria

Hybrid retrieval is ready for the answer-generation phase when:

1. Vector-only and hybrid evaluations are stored and comparable.
2. Hybrid retrieval does not reduce Recall@10 below the accepted baseline.
3. Precision@5 improves on exact, mathematical, and code-oriented queries.
4. Custom source selection has zero scope violations.
5. Empty and boilerplate content is filtered.
6. Reranker failure produces valid RRF results.
7. Context stays within its configured token budget.
8. Every context item has a stable citation and source identity.
9. PDF and AI Note evidence are visibly distinguished.
10. Insufficient evidence produces `WEAK`, `INSUFFICIENT`, or `NO_RESULTS`
    instead of pretending retrieval succeeded.
