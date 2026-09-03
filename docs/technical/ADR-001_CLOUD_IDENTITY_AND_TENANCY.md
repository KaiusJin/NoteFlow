# ADR-001: Cloud identity and tenancy boundary

- Status: Accepted for migration
- Date: 2026-09-03
- Decision owners: NoteFlow application architecture

## Context

The original application used one fixed local UUID. Domain tables consequently use `user_id` as both account identity and tenant scope, while the cloud product requires verified users, Google login and room for future shared workspaces. Replacing every domain key in one release would combine authentication, storage and all feature migrations into an unsafe flag day.

## Decision

Supabase Auth is the only credential and session authority. Spring validates its access JWT for every public API request in the `cloud` profile. Passwords, one-time codes, OAuth refresh tokens and service-role keys never enter NoteFlow tables or the browser bundle.

During the migration, a user's personal workspace has the same UUID as `auth.users.id`. The existing `user_id` domain columns therefore continue to form a real tenant boundary. `profiles`, `workspaces` and `workspace_members` establish the target model, and the Auth trigger creates all compatibility rows atomically.

The Python worker uses a separate internal credential plus an explicit workspace header. Both values are required together; ordinary Supabase JWTs cannot access `/internal/**`. Production should additionally restrict internal ingress, rotate the token and eventually replace the shared secret with Cloud Run service identity.

Local development remains unauthenticated and loopback-only under the default profile. This is an explicit development mode, not a production fallback. The `cloud` profile fails closed if JWT or internal-service configuration is missing.

## Consequences

- The browser can use standard Supabase email/password, email OTP and Google OAuth flows.
- Existing repositories receive the verified JWT subject without a cross-cutting rewrite.
- Team workspaces are not enabled until domain tables are renamed to `workspace_id` and workspace selection becomes an explicit signed/request-scoped decision.
- Flyway continues to own domain schema V1–V5; the Supabase migration owns only Auth integration tables and triggers. The deployment runbook enforces their order.
- A future migration will remove the compatibility `public.users` projection after all foreign keys point at workspace membership or Auth identity as appropriate.

## Rejected alternatives

- Copying the UWDegreeExplorer password/session tables would duplicate Supabase Auth and expand the breach surface.
- Accepting a user-supplied workspace header on public endpoints would permit tenant spoofing.
- Rewriting all domain tables before introducing authentication would make the migration too broad to review or roll back safely.
