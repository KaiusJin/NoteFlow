# Supabase Auth setup for NoteFlow

This project delegates passwords, one-time codes, sessions and Google OAuth tokens to Supabase Auth. The NoteFlow database stores only profile and workspace metadata.

## 1. Create the project and schema

Link the Supabase CLI project, then apply `supabase/migrations/202609030001_identity_and_personal_workspaces.sql`. The migration creates:

- `profiles`, with a unique normalized username and row-level security;
- `workspaces` and `workspace_members`, which establish the tenant boundary;
- an `auth.users` trigger that creates the profile and personal workspace in the same transaction;
- `username_available`, a narrowly scoped registration check available to anonymous clients.

Do not copy password hashes, email verification codes or refresh tokens into public tables.

## 2. Use a six-digit signup code

In **Authentication → Email Templates → Confirm signup**, replace the confirmation-link body with a template that displays `{{ .Token }}`. Keep **Confirm email** enabled. The web client calls `verifyOtp` with `type: signup` after the user enters the code.

Supabase's built-in mail service is suitable only for local trials and has a very low delivery limit. Configure custom SMTP before inviting users. The Auth rate-limit settings should keep the resend cooldown enabled.

## 3. Enable Google OAuth

Create a Google OAuth web client, add the callback URL displayed by **Authentication → Providers → Google**, and configure the client ID and secret there. Add both the Cloudflare Pages production origin and local development origin to the allowed origins/redirect URLs.

Google users receive a provisional private profile on first sign-in. NoteFlow immediately asks them to choose a unique username before opening the application.

## 4. Configure the frontend

Copy `apps/web-v2/.env.example` to `apps/web-v2/.env.local` and set the project URL, publishable/anon key and Spring API URL. Only the public Supabase key belongs in the frontend. Never expose the service-role key.

Cloudflare Pages should build from `apps/web-v2` with command `npm run build` and output directory `dist`. Production secrets and server-only keys belong in the Spring API or worker environment, not Pages.

## 5. Production hardening checklist

- Configure a custom SMTP provider and test delivery, expiry and resend throttling.
- Enable leaked-password protection and MFA when the product reaches private beta.
- Add Cloudflare Turnstile through Supabase CAPTCHA before public signup.
- Verify every application table has RLS in Supabase and tenant enforcement in the Spring API.
- Rotate OAuth and service-role secrets if they have ever appeared in logs or client bundles.
