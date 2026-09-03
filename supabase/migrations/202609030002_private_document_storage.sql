-- NoteFlow keeps source PDFs and generated page/region PNGs private. The
-- browser never receives the server secret; authenticated downloads are
-- authorized by the Spring API and proxied from Storage.
insert into storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
values (
    'noteflow-private',
    'noteflow-private',
    false,
    52428800,
    array['application/pdf', 'image/png']
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- No anon/authenticated storage.objects policy is intentional. Only trusted
-- API and Worker runtimes use SUPABASE_SECRET_KEY. Tenant access is checked
-- against documents.user_id before the API returns an object.
