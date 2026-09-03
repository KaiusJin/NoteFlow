-- NoteFlow cloud identity boundary. Supabase Auth owns credentials and OTPs;
-- application tables retain only public profile and tenancy metadata.

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  username text not null,
  display_name text,
  avatar_url text,
  onboarding_completed boolean not null default false,
  preferences jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint profiles_username_format check (username ~ '^[a-z0-9_]{3,24}$')
);

create unique index if not exists profiles_username_unique
  on public.profiles (lower(username));

create table if not exists public.workspaces (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  kind text not null default 'personal' check (kind in ('personal', 'team')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists workspaces_one_personal_per_owner
  on public.workspaces (owner_id) where kind = 'personal';

create table if not exists public.workspace_members (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'owner' check (role in ('owner', 'editor', 'viewer')),
  created_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);

create index if not exists workspace_members_user_id_idx
  on public.workspace_members (user_id);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

drop trigger if exists workspaces_set_updated_at on public.workspaces;
create trigger workspaces_set_updated_at
before update on public.workspaces
for each row execute function public.set_updated_at();

create or replace function public.username_available(candidate text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select
    candidate ~ '^[a-z0-9_]{3,24}$'
    and not exists (
      select 1 from public.profiles where lower(username) = lower(candidate)
    );
$$;

revoke all on function public.username_available(text) from public;
grant execute on function public.username_available(text) to anon, authenticated;

create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  requested_username text := lower(trim(coalesce(new.raw_user_meta_data ->> 'username', '')));
  provisional_username text;
  profile_is_complete boolean;
  personal_workspace_id uuid;
begin
  profile_is_complete := requested_username ~ '^[a-z0-9_]{3,24}$';

  if profile_is_complete then
    if exists (select 1 from public.profiles where lower(username) = requested_username) then
      raise exception using message = 'Username is already in use', errcode = '23505';
    end if;
    provisional_username := requested_username;
  else
    provisional_username := 'user_' || substring(replace(new.id::text, '-', '') from 1 for 12);
  end if;

  insert into public.profiles (id, username, display_name, avatar_url, onboarding_completed)
  values (
    new.id,
    provisional_username,
    nullif(trim(coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name', '')), ''),
    nullif(trim(coalesce(new.raw_user_meta_data ->> 'avatar_url', '')), ''),
    profile_is_complete
  );

  insert into public.workspaces (owner_id, name, kind)
  values (new.id, 'My workspace', 'personal')
  returning id into personal_workspace_id;

  insert into public.workspace_members (workspace_id, user_id, role)
  values (personal_workspace_id, new.id, 'owner');

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_auth_user();

alter table public.profiles enable row level security;
alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;

drop policy if exists "Users can read their profile" on public.profiles;
create policy "Users can read their profile"
on public.profiles for select
to authenticated
using ((select auth.uid()) = id);

drop policy if exists "Users can update their profile" on public.profiles;
create policy "Users can update their profile"
on public.profiles for update
to authenticated
using ((select auth.uid()) = id)
with check ((select auth.uid()) = id);

drop policy if exists "Members can read their workspaces" on public.workspaces;
create policy "Members can read their workspaces"
on public.workspaces for select
to authenticated
using (
  exists (
    select 1 from public.workspace_members
    where workspace_id = workspaces.id and user_id = (select auth.uid())
  )
);

drop policy if exists "Owners can update their workspaces" on public.workspaces;
create policy "Owners can update their workspaces"
on public.workspaces for update
to authenticated
using (owner_id = (select auth.uid()))
with check (owner_id = (select auth.uid()));

drop policy if exists "Users can read their memberships" on public.workspace_members;
create policy "Users can read their memberships"
on public.workspace_members for select
to authenticated
using (user_id = (select auth.uid()));

grant select, update on public.profiles to authenticated;
grant select, update on public.workspaces to authenticated;
grant select on public.workspace_members to authenticated;
