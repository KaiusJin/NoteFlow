# ADR-004: Deploy the PWA with Cloudflare Workers Static Assets

- Status: Accepted
- Date: 2026-09-03

## Context

NoteFlow is a client-rendered React/Vite PWA with a separate Spring Boot API and Python task worker. Cloudflare now treats Workers as its primary application platform and supports serving SPA assets without a Worker script invocation. The repository previously targeted Cloudflare Pages.

## Decision

Deploy `apps/web-v2/dist` as Cloudflare Workers Static Assets using the checked-in `wrangler.jsonc` and an exact Wrangler dependency. Configure `not_found_handling` as `single-page-application`; retain `_headers` in the built asset directory; do not define a Worker `main` entry point in the first production release.

Cloudflare Workers Builds uses:

- root directory: `apps/web-v2`;
- build command: `npm run build`;
- deploy command: `npm run deploy`;
- production branch: `main`.

The frontend receives only public build variables: `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` and `VITE_API_BASE_URL`. Server credentials remain in Google Secret Manager or their owning managed service.

## Consequences

- Static requests are served directly and do not invoke dynamic Worker code.
- SPA fallback is explicit, reproducible and checked by CI with a Wrangler dry run.
- Version preview URLs and a future edge gateway remain available without another hosting migration.
- Spring Boot, Python/LangGraph, PostgreSQL, Storage and Redis boundaries do not change.
- Adding a Worker script later requires a separate ADR, explicit CPU/request budgeting, authentication review and end-to-end tests. Cloudflare must not become a second source of business truth.

## Rollback

The PWA remains a standard `dist` directory. A rollback can deploy a prior Worker version or host the same immutable build on another static host; no database migration is coupled to this decision.
