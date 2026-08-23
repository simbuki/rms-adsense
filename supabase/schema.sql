-- RMS AdSense — Supabase schema
-- Safe to re-run top-to-bottom on a fresh project OR a partially-set-up one:
-- every create is guarded (if not exists / drop-then-add / create or replace),
-- so re-running this after an earlier partial run will not error out.
--
-- This version has the admin-related fixes already merged in:
--   - Admin status lives in its own public.admins table (not profiles.role
--     directly), checked through a non-recursive public.is_admin() helper.
--   - profiles.role is kept in sync automatically via trigger.
--   - Single-use invite codes let a new admin self-register (email +
--     password + code) instead of a developer manually promoting them.

-- =========================================================
-- Extensions
-- =========================================================
create extension if not exists "pgcrypto";

-- =========================================================
-- Clean up any leftover function overloads from earlier partial runs.
-- =========================================================
do $$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure as func_signature
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname in (
        'approve_booking', 'reject_booking', 'submit_booking',
        'next_invoice_no', 'handle_new_user', 'set_updated_at',
        'is_admin', 'sync_profile_role', 'generate_admin_invite',
        'claim_admin_invite', 'mark_invoice_paid', 'admin_create_booking'
      )
  loop
    execute format('drop function if exists %s cascade;', r.func_signature);
  end loop;
end $$;

-- =========================================================
-- Profiles (one row per auth.users row)
-- =========================================================
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  role text not null default 'client' check (role in ('client', 'admin')),
  business_name text not null default '',
  business_type text not null default '',
  county text not null default '',
  phone text not null default '',
  email text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.profiles add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_profiles_role on public.profiles (role);

alter table public.profiles enable row level security;

-- =========================================================
-- Admins (source of truth for admin access — kept separate from
-- profiles so that RLS on profiles never has to query profiles again,
-- which previously caused "infinite recursion detected in policy for
-- relation profiles" on every login attempt).
-- =========================================================
create table if not exists public.admins (
  id uuid primary key references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table public.admins enable row level security;

drop policy if exists "admins: self read" on public.admins;
create policy "admins: self read" on public.admins
  for select using (auth.uid() = id);

-- Non-recursive admin check. SECURITY DEFINER + owned by the table
-- owner means this bypasses RLS entirely when reading admins, so
-- using it inside a profiles/slots/bookings/invoices/storage policy
-- never re-triggers RLS on the table it's checking.
create or replace function public.is_admin()
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from public.admins where id = auth.uid()
  );
$$;

grant execute on function public.is_admin() to authenticated;

-- =========================================================
-- Rate limiting — a simple Postgres-backed sliding-window counter.
-- Edge functions are stateless (each invocation may run on a fresh
-- isolate), so an in-memory counter can't be trusted; this table is
-- the shared source of truth. No RLS policies are granted — it's
-- only ever touched through check_rate_limit(), never read/written
-- directly by clients.
-- =========================================================
create table if not exists public.rate_limits (
  rate_key text primary key,
  window_start timestamptz not null default now(),
  count integer not null default 0
);

alter table public.rate_limits enable row level security;

-- Returns true if the action is allowed (and records it), false if the
-- caller has hit p_max_count within the last p_window_seconds. Keys
-- are caller-chosen strings, typically "action:user_id" or
-- "action:ip_address" — combine both where an endpoint accepts
-- unauthenticated requests.
create or replace function public.check_rate_limit(
  p_key text,
  p_max_count integer,
  p_window_seconds integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.rate_limits;
begin
  select * into v_row from public.rate_limits where rate_key = p_key for update;

  if not found then
    insert into public.rate_limits (rate_key, window_start, count) values (p_key, now(), 1);
    return true;
  end if;

  if now() - v_row.window_start > make_interval(secs => p_window_seconds) then
    update public.rate_limits set window_start = now(), count = 1 where rate_key = p_key;
    return true;
  end if;

  if v_row.count >= p_max_count then
    return false;
  end if;

  update public.rate_limits set count = count + 1 where rate_key = p_key;
  return true;
end;
$$;

grant execute on function public.check_rate_limit(text, integer, integer) to authenticated, service_role;

-- =========================================================
-- Profiles policies
-- =========================================================
drop policy if exists "profiles: read own" on public.profiles;
create policy "profiles: read own" on public.profiles
  for select using (auth.uid() = id);

drop policy if exists "profiles: admins read all" on public.profiles;
create policy "profiles: admins read all" on public.profiles
  for select using ( public.is_admin() );

drop policy if exists "profiles: update own" on public.profiles;
create policy "profiles: update own" on public.profiles
  for update using (auth.uid() = id);

-- Keep updated_at current on every edit.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_profiles_updated_at on public.profiles;
create trigger trg_profiles_updated_at
  before update on public.profiles
  for each row execute procedure public.set_updated_at();

-- Keep profiles.role in sync whenever someone is added to / removed
-- from public.admins, so the front end's session.role keeps working
-- exactly as before without any JS changes.
create or replace function public.sync_profile_role()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if tg_op = 'INSERT' then
    update public.profiles set role = 'admin' where id = new.id;
  elsif tg_op = 'DELETE' then
    update public.profiles set role = 'client' where id = old.id;
  end if;
  return coalesce(new, old);
end;
$$;

drop trigger if exists trg_admins_sync_role on public.admins;
create trigger trg_admins_sync_role
  after insert or delete on public.admins
  for each row execute procedure public.sync_profile_role();

-- Auto-create a profile row whenever a new auth user is created.
-- Business fields are pulled from the signUp() options.data metadata.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, role, business_name, business_type, county, phone, email)
  values (
    new.id,
    'client',
    coalesce(new.raw_user_meta_data->>'business_name', ''),
    coalesce(new.raw_user_meta_data->>'business_type', ''),
    coalesce(new.raw_user_meta_data->>'county', ''),
    coalesce(new.raw_user_meta_data->>'phone', ''),
    new.email
  );
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- One-time backfill for any accounts that registered before the trigger
-- above existed (harmless no-op once everyone has a profile row).
insert into public.profiles (id, role, business_name, business_type, county, phone, email)
select id, 'client', '', '', '', '', email
from auth.users
where id not in (select id from public.profiles);

-- One-time backfill: carry over anyone already marked role = 'admin'
-- in profiles (e.g. from an older version of this schema) into the
-- new admins table. Harmless no-op on a fresh install.
insert into public.admins (id)
select id from public.profiles
where role = 'admin'
on conflict (id) do nothing;

-- =========================================================
-- Stations
-- =========================================================
create table if not exists public.stations (
  id integer generated always as identity primary key,
  name text not null,
  type text not null check (type in ('TV', 'Radio')),
  note text not null default ''
);

create index if not exists idx_stations_type on public.stations (type);

alter table public.stations enable row level security;

drop policy if exists "stations: public read" on public.stations;
create policy "stations: public read" on public.stations
  for select using (true);

drop policy if exists "stations: admins write" on public.stations;
create policy "stations: admins write" on public.stations
  for all using ( public.is_admin() ) with check ( public.is_admin() );

-- =========================================================
-- Slots
-- =========================================================
create table if not exists public.slots (
  id integer generated always as identity primary key,
  station_id integer not null references public.stations (id) on delete cascade,
  label text not null,
  day text not null,
  time text not null,
  duration integer not null,
  price integer not null,
  tier text not null default 'standard',
  status text not null default 'open' check (status in ('open', 'pending', 'booked'))
);

create index if not exists idx_slots_station_id on public.slots (station_id);
create index if not exists idx_slots_status on public.slots (status);

alter table public.slots drop constraint if exists slots_duration_positive;
alter table public.slots
  add constraint slots_duration_positive check (duration > 0) not valid;
alter table public.slots validate constraint slots_duration_positive;

alter table public.slots drop constraint if exists slots_price_non_negative;
alter table public.slots
  add constraint slots_price_non_negative check (price >= 0) not valid;
alter table public.slots validate constraint slots_price_non_negative;

alter table public.slots enable row level security;

drop policy if exists "slots: public read" on public.slots;
create policy "slots: public read" on public.slots
  for select using (true);

drop policy if exists "slots: admins write" on public.slots;
create policy "slots: admins write" on public.slots
  for all using ( public.is_admin() ) with check ( public.is_admin() );

-- =========================================================
-- Bookings
-- =========================================================
create table if not exists public.bookings (
  id bigint generated always as identity primary key,
  slot_id integer not null references public.slots (id) on delete cascade,
  client_id uuid references auth.users (id) on delete cascade,
  business_name text not null,
  business_type text not null,
  county text not null,
  email text not null,
  phone text not null,
  ad_description text not null,
  file_name text not null default '',
  file_path text,
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  submitted_at date not null default current_date
);

-- Upgrade path: if bookings already existed with client_id NOT NULL
-- (from before admin-entered walk-in bookings existed), relax it.
alter table public.bookings alter column client_id drop not null;

-- Schema-level input validation: length limits and a basic email-format
-- check, enforced regardless of which entry point (submit_booking(),
-- admin_create_booking(), or a raw insert) is used.
alter table public.bookings drop constraint if exists bookings_field_lengths;
alter table public.bookings add constraint bookings_field_lengths check (
  char_length(business_name) between 1 and 200
  and char_length(business_type) <= 100
  and char_length(county) <= 100
  and char_length(phone) <= 30
  and char_length(ad_description) <= 2000
  and char_length(file_name) <= 255
) not valid;
alter table public.bookings validate constraint bookings_field_lengths;

alter table public.bookings drop constraint if exists bookings_email_format;
alter table public.bookings add constraint bookings_email_format check (
  email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'
) not valid;
alter table public.bookings validate constraint bookings_email_format;

create index if not exists idx_bookings_slot_id on public.bookings (slot_id);
create index if not exists idx_bookings_client_id on public.bookings (client_id);
create index if not exists idx_bookings_status on public.bookings (status);

-- Belt-and-suspenders against double-booking: even if two requests somehow
-- raced past submit_booking()'s row lock, the database itself won't allow
-- two simultaneously-pending bookings on the same slot. If this fails with
-- a uniqueness violation, resolve duplicate pending bookings first (see
-- README) before re-running.
create unique index if not exists uq_bookings_one_pending_per_slot
  on public.bookings (slot_id)
  where status = 'pending';

alter table public.bookings enable row level security;

drop policy if exists "bookings: client reads own" on public.bookings;
create policy "bookings: client reads own" on public.bookings
  for select using (auth.uid() = client_id);

drop policy if exists "bookings: admin reads all" on public.bookings;
create policy "bookings: admin reads all" on public.bookings
  for select using ( public.is_admin() );

drop policy if exists "bookings: client inserts own" on public.bookings;
create policy "bookings: client inserts own" on public.bookings
  for insert with check (
    auth.uid() = client_id
    and status = 'pending'
    and exists (select 1 from public.slots s where s.id = slot_id and s.status = 'open')
  );

drop policy if exists "bookings: admin updates" on public.bookings;
create policy "bookings: admin updates" on public.bookings
  for update using ( public.is_admin() );

-- Only allow booking a slot that is currently open, and flip it to pending atomically.
create or replace function public.submit_booking(
  p_slot_id integer,
  p_business_name text,
  p_business_type text,
  p_county text,
  p_email text,
  p_phone text,
  p_ad_description text,
  p_file_name text,
  p_file_path text
)
returns public.bookings
language plpgsql
security definer set search_path = public
as $$
declare
  v_slot public.slots;
  v_booking public.bookings;
begin
  if not public.check_rate_limit('submit_booking:' || auth.uid()::text, 5, 600) then
    raise exception 'Too many booking requests — please wait a few minutes and try again.';
  end if;

  select * into v_slot from public.slots where id = p_slot_id for update;
  if not found then
    raise exception 'Slot not found';
  end if;
  if v_slot.status <> 'open' then
    raise exception 'Slot is no longer open';
  end if;

  update public.slots set status = 'pending' where id = p_slot_id;

  insert into public.bookings (
    slot_id, client_id, business_name, business_type, county, email, phone,
    ad_description, file_name, file_path, status, submitted_at
  ) values (
    p_slot_id, auth.uid(), p_business_name, p_business_type, p_county, p_email, p_phone,
    p_ad_description, p_file_name, p_file_path, 'pending', current_date
  ) returning * into v_booking;

  return v_booking;
end;
$$;

grant execute on function public.submit_booking to authenticated;

-- =========================================================
-- Invoices
-- =========================================================
create table if not exists public.invoices (
  id bigint generated always as identity primary key,
  booking_id bigint not null references public.bookings (id) on delete cascade,
  invoice_no text not null unique,
  amount integer not null,
  issued_date date not null default current_date,
  payment_status text not null default 'unpaid' check (payment_status in ('unpaid', 'paid')),
  mpesa_receipt text,
  mpesa_checkout_request_id text
);

create index if not exists idx_invoices_booking_id on public.invoices (booking_id);
create index if not exists idx_invoices_payment_status on public.invoices (payment_status);

-- For payments confirmed manually by an admin (cash, bank transfer, etc)
-- outside the automatic M-Pesa callback flow.
alter table public.invoices add column if not exists payment_method text not null default 'mpesa' check (payment_method in ('mpesa', 'manual'));
alter table public.invoices add column if not exists marked_paid_by uuid references auth.users (id);
alter table public.invoices add column if not exists marked_paid_at timestamptz;

alter table public.invoices drop constraint if exists invoices_amount_non_negative;
alter table public.invoices
  add constraint invoices_amount_non_negative check (amount >= 0) not valid;
alter table public.invoices validate constraint invoices_amount_non_negative;

alter table public.invoices enable row level security;

drop policy if exists "invoices: client reads own" on public.invoices;
create policy "invoices: client reads own" on public.invoices
  for select using (
    exists (
      select 1 from public.bookings b
      where b.id = invoices.booking_id and b.client_id = auth.uid()
    )
  );

drop policy if exists "invoices: admin reads all" on public.invoices;
create policy "invoices: admin reads all" on public.invoices
  for select using ( public.is_admin() );

-- Invoice numbers: INV-0001, INV-0002, ...
create sequence if not exists public.invoice_no_seq;

create or replace function public.next_invoice_no()
returns text
language sql
as $$
  select 'INV-' || lpad(nextval('public.invoice_no_seq')::text, 4, '0');
$$;

-- Admin-only: directly create and auto-approve a booking, for phone-in
-- or walk-in clients who don't have (or don't need) an account. Skips
-- the pending queue entirely — slot goes straight to "booked" and an
-- invoice is issued immediately, same as approve_booking() would do.
create or replace function public.admin_create_booking(
  p_slot_id integer,
  p_business_name text,
  p_business_type text,
  p_county text,
  p_email text,
  p_phone text,
  p_ad_description text
)
returns public.invoices
language plpgsql
security definer set search_path = public
as $$
declare
  v_slot public.slots;
  v_booking public.bookings;
  v_invoice public.invoices;
begin
  if not public.is_admin() then
    raise exception 'Admin only';
  end if;

  select * into v_slot from public.slots where id = p_slot_id for update;
  if not found then
    raise exception 'Slot not found';
  end if;
  if v_slot.status <> 'open' then
    raise exception 'Slot is no longer open';
  end if;

  insert into public.bookings (
    slot_id, client_id, business_name, business_type, county, email, phone,
    ad_description, file_name, file_path, status, submitted_at
  ) values (
    p_slot_id, null, p_business_name, p_business_type, p_county, p_email, p_phone,
    p_ad_description, '', null, 'approved', current_date
  ) returning * into v_booking;

  update public.slots set status = 'booked' where id = p_slot_id;

  insert into public.invoices (booking_id, invoice_no, amount, issued_date, payment_status)
  values (v_booking.id, public.next_invoice_no(), v_slot.price, current_date, 'unpaid')
  returning * into v_invoice;

  return v_invoice;
end;
$$;

grant execute on function public.admin_create_booking to authenticated;

-- Admin-only: approve a pending booking, book the slot, and issue an invoice.
create or replace function public.approve_booking(p_booking_id bigint)
returns public.invoices
language plpgsql
security definer set search_path = public
as $$
declare
  v_booking public.bookings;
  v_slot public.slots;
  v_invoice public.invoices;
begin
  if not public.is_admin() then
    raise exception 'Admin only';
  end if;

  select * into v_booking from public.bookings where id = p_booking_id for update;
  if not found or v_booking.status <> 'pending' then
    raise exception 'Booking not found or not pending';
  end if;

  select * into v_slot from public.slots where id = v_booking.slot_id for update;

  update public.bookings set status = 'approved' where id = p_booking_id;
  update public.slots set status = 'booked' where id = v_slot.id;

  insert into public.invoices (booking_id, invoice_no, amount, issued_date, payment_status)
  values (p_booking_id, public.next_invoice_no(), v_slot.price, current_date, 'unpaid')
  returning * into v_invoice;

  return v_invoice;
end;
$$;

grant execute on function public.approve_booking to authenticated;

-- Admin-only: reject a pending booking and free the slot.
create or replace function public.reject_booking(p_booking_id bigint)
returns public.bookings
language plpgsql
security definer set search_path = public
as $$
declare
  v_booking public.bookings;
begin
  if not public.is_admin() then
    raise exception 'Admin only';
  end if;

  select * into v_booking from public.bookings where id = p_booking_id for update;
  if not found or v_booking.status <> 'pending' then
    raise exception 'Booking not found or not pending';
  end if;

  update public.bookings set status = 'rejected' where id = p_booking_id;
  update public.slots set status = 'open' where id = v_booking.slot_id;

  select * into v_booking from public.bookings where id = p_booking_id;
  return v_booking;
end;
$$;

grant execute on function public.reject_booking to authenticated;

-- Admin-only: manually confirm payment for an invoice paid outside
-- M-Pesa (cash, bank transfer, etc). The automatic M-Pesa callback
-- path (which uses the service-role key and bypasses RLS) is separate
-- and untouched by this.
create or replace function public.mark_invoice_paid(p_invoice_id bigint)
returns public.invoices
language plpgsql
security definer set search_path = public
as $$
declare
  v_invoice public.invoices;
begin
  if not public.is_admin() then
    raise exception 'Admin only';
  end if;

  select * into v_invoice from public.invoices where id = p_invoice_id for update;
  if not found then
    raise exception 'Invoice not found';
  end if;
  if v_invoice.payment_status = 'paid' then
    raise exception 'Invoice is already marked paid';
  end if;

  update public.invoices
  set payment_status = 'paid',
      payment_method = 'manual',
      marked_paid_by = auth.uid(),
      marked_paid_at = now()
  where id = p_invoice_id
  returning * into v_invoice;

  return v_invoice;
end;
$$;

grant execute on function public.mark_invoice_paid(bigint) to authenticated;

-- The mpesa-callback edge function marks invoices paid using the service-role
-- key, which bypasses RLS, so no authenticated "mark paid" policy is needed here.

-- =========================================================
-- Admin invite codes — lets a new admin self-register (email +
-- password + code) instead of a developer manually running an
-- "update profiles set role = 'admin'" or editing the admins table
-- by hand. No RLS policies are granted on this table: it's only ever
-- touched through the two SECURITY DEFINER functions below.
-- =========================================================
create table if not exists public.admin_invite_codes (
  code text primary key,
  created_at timestamptz not null default now(),
  used_at timestamptz,
  used_by uuid references auth.users (id)
);

alter table public.admin_invite_codes enable row level security;

-- Existing admin generates a fresh single-use code to hand to a new hire.
create or replace function public.generate_admin_invite()
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_code text;
begin
  if not public.is_admin() then
    raise exception 'Admin only';
  end if;

  v_code := encode(gen_random_bytes(9), 'base64');
  v_code := replace(replace(replace(v_code, '/', '_'), '+', '-'), '=', '');

  insert into public.admin_invite_codes (code) values (v_code);
  return v_code;
end;
$$;

grant execute on function public.generate_admin_invite() to authenticated;

-- New user redeems a code right after signing up, granting THEMSELVES
-- (auth.uid()) admin access. One-time use.
create or replace function public.claim_admin_invite(p_code text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.admin_invite_codes;
begin
  if auth.uid() is null then
    raise exception 'Not signed in';
  end if;

  select * into v_row from public.admin_invite_codes
  where code = p_code and used_at is null
  for update;

  if not found then
    raise exception 'Invalid or already-used invite code';
  end if;

  update public.admin_invite_codes
  set used_at = now(), used_by = auth.uid()
  where code = p_code;

  insert into public.admins (id) values (auth.uid())
  on conflict (id) do nothing;

  return true;
end;
$$;

grant execute on function public.claim_admin_invite(text) to authenticated;

-- =========================================================
-- Storage: creative-files (private bucket)
-- =========================================================
insert into storage.buckets (id, name, public)
values ('creative-files', 'creative-files', false)
on conflict (id) do nothing;

-- Clients upload into a folder named after their own user id: {uid}/filename
drop policy if exists "creative-files: client uploads own folder" on storage.objects;
create policy "creative-files: client uploads own folder"
  on storage.objects for insert
  with check (
    bucket_id = 'creative-files'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists "creative-files: client reads own folder" on storage.objects;
create policy "creative-files: client reads own folder"
  on storage.objects for select
  using (
    bucket_id = 'creative-files'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

drop policy if exists "creative-files: admin reads all" on storage.objects;
create policy "creative-files: admin reads all"
  on storage.objects for select
  using (
    bucket_id = 'creative-files'
    and public.is_admin()
  );

-- =========================================================
-- Demo seed — replace with the real RMS catalogue before production
-- =========================================================
insert into public.stations (name, type, note)
select v.name, v.type, v.note
from (values
  ('Citizen TV', 'TV', 'Flagship national TV, Kiswahili/English'),
  ('Inooro TV', 'TV', 'Kikuyu-language TV'),
  ('Citizen Radio', 'Radio', 'National English/Kiswahili radio'),
  ('Inooro FM', 'Radio', 'Kikuyu-language radio'),
  ('Ramogi FM', 'Radio', 'Dholuo-language radio'),
  ('Musyi FM', 'Radio', 'Kikamba-language radio'),
  ('Ghetto Radio', 'Radio', 'Urban youth radio, Nairobi')
) as v(name, type, note)
where not exists (select 1 from public.stations s where s.name = v.name);

insert into public.slots (station_id, label, day, time, duration, price, tier, status)
select s.id, v.label, v.day, v.time, v.duration, v.price, v.tier, 'open'
from (values
  ('Citizen TV', 'Prime evening news break', 'Mon', '19:00', 30, 45000, 'premium'),
  ('Citizen TV', 'Mid-morning slot', 'Tue', '10:30', 20, 18000, 'standard'),
  ('Inooro TV', 'Drivetime break', 'Wed', '17:30', 30, 15000, 'standard'),
  ('Citizen Radio', 'Breakfast show', 'Mon', '07:15', 30, 22000, 'premium'),
  ('Inooro FM', 'Midday break', 'Thu', '13:00', 20, 9000, 'standard'),
  ('Ramogi FM', 'Evening drivetime', 'Fri', '18:00', 30, 8500, 'standard'),
  ('Musyi FM', 'Morning break', 'Tue', '08:30', 20, 7000, 'standard'),
  ('Ghetto Radio', 'Weekend afternoon', 'Sat', '15:00', 30, 11000, 'standard')
) as v(station_name, label, day, time, duration, price, tier)
join public.stations s on s.name = v.station_name
where not exists (
  select 1 from public.slots sl
  where sl.station_id = s.id and sl.label = v.label and sl.day = v.day and sl.time = v.time
);

-- =========================================================
-- Bootstrap your first admin
-- =========================================================
-- generate_admin_invite() can't be called until an admin already
-- exists. Run this once with your own random string, then redeem it
-- on admin-login.html under "Create admin account":
--
--   insert into public.admin_invite_codes (code) values ('replace-with-a-long-random-string');
--
-- After that, existing admins can mint further codes from the browser
-- console while logged in:
--
--   supabase.rpc('generate_admin_invite').then(r => console.log(r.data));
