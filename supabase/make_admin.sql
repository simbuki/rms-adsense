-- Promote a registered account to admin. This is the only way to add an
-- admin: there is no self-service admin signup.
--
-- Replace the email below with the staff email that should become admin.
-- The account must already exist (they must have registered once
-- through login.html).

insert into public.admins (id)
select id from auth.users where email = 'staff@example.com'
on conflict (id) do nothing;

-- To demote an admin back to a regular client:
-- delete from public.admins where id = (select id from auth.users where email = 'staff@example.com');
