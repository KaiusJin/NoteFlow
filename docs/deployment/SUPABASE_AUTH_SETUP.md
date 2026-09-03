# Supabase Auth setup for NoteFlow

This project delegates passwords, one-time codes, sessions and Google OAuth tokens to Supabase Auth. The NoteFlow database stores only profile and workspace metadata.

## 1. Create the project and schema

The current transition has two explicit schema owners: Flyway owns the NoteFlow domain tables and Supabase owns the managed `auth` and `storage` schemas. Apply Spring/Flyway migrations V1–V6 to the empty Supabase database first, then link the Supabase CLI project and apply the ordered files under `supabase/migrations/`. Never let both tools define the same table.

The Supabase migration creates:

- `profiles`, with a unique normalized username and row-level security;
- `workspaces` and `workspace_members`, which establish the tenant boundary;
- an `auth.users` trigger that creates the profile and personal workspace in the same transaction;
- `username_available`, a narrowly scoped registration check available to anonymous clients.

It also maintains a credential-free row in the legacy `public.users` table. This is a compatibility projection for existing foreign keys; Supabase `auth.users` remains the identity source. The personal workspace initially uses the same UUID as the Auth user so existing `user_id` tenant columns remain enforceable during the workspace-column migration.

The second Supabase migration creates the private `noteflow-private` Storage bucket. Source PDFs and generated PNGs use deterministic object paths under `users/<user-id>/documents/<document-id>/`. There are intentionally no browser-facing `storage.objects` policies: Spring authorizes the document owner and proxies image reads, while the trusted API and Worker use a server-only secret key.

Do not copy password hashes, email verification codes or refresh tokens into public tables.

## 2. Use a six-digit signup code

In **Authentication → Email Templates → Confirm signup**, replace the confirmation-link body with a template that displays `{{ .Token }}`. Keep **Confirm email** enabled. The web client calls `verifyOtp` with `type: signup` after the user enters the code.

Supabase's built-in mail service is suitable only for local trials and has a very low delivery limit. Configure custom SMTP before inviting users. The Auth rate-limit settings should keep the resend cooldown enabled.

## 3. Enable Google OAuth

Create a Google OAuth web client, add the callback URL displayed by **Authentication → Providers → Google**, and configure the client ID and secret there. Add both the Cloudflare Worker production origin and local development origin to the allowed origins/redirect URLs.

Google users receive a provisional private profile on first sign-in. NoteFlow immediately asks them to choose a unique username before opening the application.

## 4. Configure the frontend

Copy `apps/web-v2/.env.example` to `apps/web-v2/.env.local` and set the project URL, publishable/anon key and Spring API URL. Only the public Supabase key belongs in the frontend. Never expose a Supabase secret or legacy service-role key.

Cloudflare Workers Builds should use `apps/web-v2` as the root directory, `npm run build` as the build command, and `npm run deploy` as the deploy command. `wrangler.jsonc` declares `dist` as Static Assets and enables explicit SPA fallback without invoking Worker code. Add `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` and `VITE_API_BASE_URL` as build variables. Production secrets and server-only keys belong in the Spring API or Python worker environment, never in the Cloudflare build or browser bundle.

Configure both trusted runtimes with `SUPABASE_URL`, `SUPABASE_SECRET_KEY` and `SUPABASE_STORAGE_BUCKET=noteflow-private`. Prefer Supabase's current `sb_secret_...` key. `SUPABASE_SERVICE_ROLE_KEY` is accepted only as a legacy migration fallback. Cloud source documents are stored as `supabase://` object references in PostgreSQL; a Worker job downloads one source to ephemeral disk, uploads deterministic derived PNG objects, and removes the temporary directory when the task finishes.

## 5. Production hardening checklist

- Configure a custom SMTP provider and test delivery, expiry and resend throttling.
- Enable leaked-password protection and MFA when the product reaches private beta.
- Add Cloudflare Turnstile through Supabase CAPTCHA before public signup.
- Verify every application table has RLS in Supabase and tenant enforcement in the Spring API.
- Rotate OAuth, Supabase secret and legacy service-role credentials if they have ever appeared in logs or client bundles.
