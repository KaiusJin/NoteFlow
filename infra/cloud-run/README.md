# Cloud Run deployment contract

These files define the production boundary; they are reviewable templates, not credentials and not an automatic deployment. Replacing `PROJECT_ID`, `PROJECT_NUMBER`, `REGION`, hostnames, image tag and Supabase project reference is required before `gcloud run services replace` or `gcloud run jobs replace`.

## Runtime topology

- `noteflow-api`: public Cloud Run ingress, `minScale=0`, `maxScale=1`. “Public” only bypasses Google IAM at the HTTP edge; Spring still requires a valid Supabase JWT for application routes.
- `noteflow-worker`: non-HTTP Cloud Run Job, one task per execution, at most one execution admitted by Redis cooldown under normal operation.
- `noteflow-api@...`: may read its required Secret Manager versions and has `roles/run.invoker` on the worker job only.
- `noteflow-worker@...`: may read its required secrets; it receives no permission to start jobs or administer Cloud Run.

Google documents `roles/run.invoker` as sufficient for `jobs.run`, including calls that obtain the access token from the container metadata server. Keep the grant on the individual job rather than the whole project: [execute Cloud Run jobs](https://cloud.google.com/run/docs/execute/jobs). Cloud Run recommends Secret Manager for runtime credentials and version-pinned secret references: [configure secrets](https://cloud.google.com/run/docs/configuring/services/secrets).

## One-time provisioning order

1. Enable Cloud Run, Cloud Build, Artifact Registry and Secret Manager APIs. A billing account may be required even when usage remains inside free tiers.
2. Create one Artifact Registry Docker repository and two distinct service accounts.
3. Create these Secret Manager secrets: `noteflow-database-url`, `noteflow-database-user`, `noteflow-database-password`, `noteflow-redis-url`, `noteflow-internal-token`, `noteflow-supabase-secret`. Add AI provider secrets only when that provider is enabled.
4. Grant each runtime service account `roles/secretmanager.secretAccessor` on only the individual secrets it consumes.
5. Apply Flyway V1–V6, then the ordered Supabase migrations. Configure Auth/Google/SMTP before exposing signup.
6. Build immutable API and Worker image tags from their module contexts:

   ```bash
   gcloud builds submit services/api \
     --tag REGION-docker.pkg.dev/PROJECT_ID/noteflow/api:GIT_SHA
   gcloud builds submit services/worker \
     --tag REGION-docker.pkg.dev/PROJECT_ID/noteflow/worker:GIT_SHA
   ```

7. Replace placeholders in `worker.job.yaml.example`, deploy the job, then grant only the API identity permission to execute it:

   ```bash
   gcloud run jobs replace worker.job.yaml --region REGION
   gcloud run jobs add-iam-policy-binding noteflow-worker \
     --region REGION \
     --member serviceAccount:noteflow-api@PROJECT_ID.iam.gserviceaccount.com \
     --role roles/run.invoker
   ```

8. Replace placeholders in `api.service.yaml.example`, deploy the service, then allow HTTP ingress. Application access is still enforced by Spring Security:

   ```bash
   gcloud run services replace api.service.yaml --region REGION
   gcloud run services add-iam-policy-binding noteflow-api \
     --region REGION \
     --member allUsers \
     --role roles/run.invoker
   ```

9. Put the resulting API HTTPS URL into `VITE_API_BASE_URL`, deploy `apps/web-v2/dist` to Cloudflare Pages, and set the exact Pages origin as `NOTEFLOW_ALLOWED_ORIGINS`.

## Required smoke test

Use a non-production account to complete this chain before inviting users:

1. email signup → six-digit confirmation → username onboarding;
2. Google login and logout;
3. PDF upload returns `202` and a task id;
4. PostgreSQL outbox publishes to the correct Redis priority list;
5. API calls `jobs.run`, one worker execution starts, downloads the private source object and exits after its work drains;
6. task reaches `COMPLETED`, generated PNGs have `supabase://` paths, and the authenticated browser can load them only through the API;
7. wrong-user JWT receives a not-found response for the same document/asset;
8. retry one deliberately interrupted worker and verify execution lease recovery prevents stale writes.

Do not use `latest` for container images or secrets in production. Roll back by reapplying the previous image tag; database migrations require a separately reviewed forward repair.
