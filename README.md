# RMS AdSense — Supabase-ready

This is the RMS AdSense front end converted from the supplied localStorage implementation to Supabase.

`assets/config.js` is included, but as a template — fill in your actual `SUPABASE_URL` and `SUPABASE_ANON_KEY`.

`assets/styles.css` is included and was built from scratch (the original wasn't shared in this conversation) — see "About the design" below.

Both M-Pesa edge functions (`mpesa-stk`, `mpesa-callback`) are included as you provided them, with `mpesa-callback` additionally recording `payment_method: 'mpesa'` on confirmation so it lines up with the manual "mark as paid" feature on the admin side.

## What is included

- `index.html` — landing page
- `login.html` — client Supabase Auth login/registration
- `admin-login.html` — **new:** separate admin login, plus admin self-registration via a single-use invite code (see below)
- `browse.html` — live Supabase slot catalogue + booking + creative upload
- `dashboard.html` — client's live bookings/invoices
- `admin.html` — admin queue, catalogue and invoices
- `payment.html` — M-Pesa STK payment UI
- `assets/config.js` — Supabase URL/anon key configuration (template — fill in your values)
- `assets/app-data.js` — Supabase data layer, **updated** with `registerAdmin()`
- `supabase/schema.sql` — full schema: tables, RLS, RPCs, storage policy, demo seed, and the admin-related fixes below, safe to run top-to-bottom on a fresh project
- `supabase/make_admin.sql` — promote a registered account to admin directly via SQL (alternative to the invite-code flow)

## What changed in this round

1. **Fixed a login bug:** every login was failing with *"Logged in, but your profile could not be loaded."* The old `"profiles: admins read all"` RLS policy queried `profiles` from inside its own policy, causing Postgres to recurse infinitely (`infinite recursion detected in policy for relation "profiles"`). Every admin-check policy across the schema had the same problem.
2. **Added a dedicated `public.admins` table.** Admin status now lives here instead of being embedded in `profiles.role`. A `public.is_admin()` helper function (SECURITY DEFINER, bypasses RLS) is used in every policy that needs to check admin status, so the recursion can't happen again. `profiles.role` is kept in sync automatically by a trigger — nothing in the front end needed to change.
3. **Added `admin-login.html`** — a separate login page for staff that goes straight to `admin.html`, plus a "Create admin account" tab.
4. **Added single-use admin invite codes.** Since a public "create an admin account" form with no protection would let anyone grant themselves full admin access, account creation on `admin-login.html` requires a code an existing admin generates and shares privately. See "Setting up your first admin" below.

`admin-login.html` is intentionally **not linked** from the site nav (`renderNav()` in `app-data.js` doesn't reference it) — it's only reachable by direct URL.

## About the design

`assets/styles.css` is a from-scratch design system built to match every class name and CSS variable the HTML already expects (`.card`, `.badge-*`, `.pill-tab`, `--signal`, `--open`, etc.) — the "kitenge bold" direction: warm color-blocking inspired by East African textile boldness rather than generic dashboard styling.

- **Palette**: burnt orange page canvas, deep teal chrome (nav, footer, dark buttons), gold as the primary accent, cream card surfaces. Status badges use a small solid square (not a rounded dot) as the indicator, echoing the hard-edged geometric blocking that runs through the palette.
- **Type**: Archivo Black carries headlines and prices — the loudest voice on the page; Archivo (600/700) handles everything else; IBM Plex Mono is reserved for data only (invoice numbers, station eyebrows).
- **Shape language**: square corners throughout (no border-radius) — deliberate, in keeping with the bold color-block aesthetic rather than a softer rounded-card look.

If you have your own brand guidelines or an existing stylesheet you'd rather use instead, swap this file out — nothing else in the project depends on its specific colors, only on the class names and CSS variables it defines.

One honest note: this design leans bold and saturated — an orange page background with teal/gold chrome throughout every screen, including dense admin forms and long lists. It reads strikingly on landing/marketing pages; on data-heavy screens (the admin catalogue, invoices list) it's a stronger visual statement than a typical neutral dashboard. If it feels too intense once you're actually using the admin side day-to-day, an easy middle ground is toning the page background down to a lighter tint of the same orange while keeping the teal/gold/cream identity elsewhere — say the word and I'll make that adjustment.

## Setup

1. Create a Supabase project.
2. Open SQL Editor and run `supabase/schema.sql`.
3. In Supabase Authentication, configure your email confirmation settings.
4. The `creative-files` storage bucket is created by the SQL script automatically.
5. Edit `assets/config.js` with your project's URL and anon/public key.
6. Deploy the edge functions:
   ```bash
   supabase functions deploy mpesa-stk
   supabase functions deploy mpesa-callback --no-verify-jwt
   ```
   (`--no-verify-jwt` on the callback because Safaricom can't send a Supabase JWT — see the M-Pesa setup section below for the required secrets.)
7. Serve the folder through HTTP rather than opening the HTML files directly.

Example local server:

```bash
python -m http.server 8080
```

Then open `http://localhost:8080`.

## Setting up your first admin

`generate_admin_invite()` requires an existing admin to call it — so for the very first admin, run this once in the SQL Editor with your own random string:

```sql
insert into public.admin_invite_codes (code) values ('replace-with-a-long-random-string');
```

Then go to `admin-login.html` → "Create admin account" → enter that code along with an email and password.

After that, any admin can generate further one-time codes from the browser console while logged in:

```js
supabase.rpc('generate_admin_invite').then(r => console.log(r.data));
```

Share the printed code privately with the next person who needs admin access.

Prefer not to use self-service signup at all? Skip the invite flow and use `supabase/make_admin.sql` instead — it promotes an existing registered account directly via SQL.

## M-Pesa setup

The front end does not contain Daraja secrets. Configure the Supabase Edge Function secrets:

- `MPESA_CONSUMER_KEY`
- `MPESA_CONSUMER_SECRET`
- `MPESA_SHORTCODE`
- `MPESA_PASSKEY`
- `MPESA_CALLBACK_URL`
- `MPESA_BASE_URL` — sandbox or production Daraja base URL
- `MPESA_TRANSACTION_TYPE` — normally `CustomerPayBillOnline` for a PayBill integration

Deploy both functions and make the callback URL point to the deployed `mpesa-callback` function.

The M-Pesa flow is real only after those credentials and the Safaricom callback configuration are supplied. The project deliberately does not fake a successful payment in JavaScript.

## Card payments

The supplied project had a card UI, but no gateway was specified. This version leaves card processing disabled rather than collecting card data in the browser. Connect a PCI-compliant provider through a Supabase Edge Function before enabling card payments.

## Important security rules

- Never put the Supabase service-role key in `assets/config.js`.
- Never put Daraja consumer secrets or passkeys in browser JavaScript.
- Keep `creative-files` private.
- Keep admin invite codes private — anyone who redeems one gets full admin access.
- Replace the demo station/slot seed with the actual RMS catalogue before production.
- Use HTTPS for deployment.
