-- Promote a registered account to admin directly (bypassing the invite
-- code flow — useful for scripted setups or if you'd rather not use
-- admin-login.html's self-service signup).
--
-- Replace the email below with the staff email that should become admin.
-- The account must already exist (they must have registered once,
-- through either login.html or admin-login.html).

insert into public.admins (id)
select id from auth.users where email = 'staff@example.com'
on conflict (id) do nothing;

-- To demote an admin back to a regular client:
-- delete from public.admins where id = (select id from auth.users where email = 'staff@example.com');

-- Prefer self-service instead? Skip this file entirely and use
-- admin-login.html's "Create admin account" tab with an invite code —
-- see the bottom of schema.sql for how to generate one.
