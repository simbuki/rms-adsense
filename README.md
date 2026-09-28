# RMS AdSense — Airtime Booking Marketplace

A self-serve marketplace for booking TV and radio airtime slots across Royal Media Services. Clients browse the slot catalogue, request bookings, upload creatives and pay invoices by M-Pesa or card; admins approve bookings, manage the catalogue and track invoices.

## Tech stack

| Layer | Technology | Where it runs |
|-------|-----------|---------------|
| Frontend | Static HTML/JS in `public/`, built with Astro | Vercel |
| Data + auth | Supabase (Postgres, Auth, Storage, RLS) | Supabase |
| Backend | Python Flask (`backend/app.py`, `backend/paystack.py`): M-Pesa STK push and callback, Paystack card payments, password reset | Render |
| Payments | Safaricom Daraja (M-Pesa STK push), Paystack (cards) | — |

The browser talks to Supabase directly (with the anon key and row-level security) for everything except the operations that need secrets: M-Pesa, card payments and password-reset emails go through the Flask backend, which holds the Supabase service-role key, Daraja credentials and the Paystack secret key.

## Project structure

```
.
├── public/                   # Served as-is by Astro
│   ├── index.html            # Landing page
│   ├── login.html            # Client login / registration
│   ├── browse.html           # Slot catalogue, booking, creative upload
│   ├── dashboard.html        # Client's bookings and invoices
│   ├── payment.html          # M-Pesa and card payment UI
│   ├── reset-password.html   # Set a new password from the reset email link
│   ├── admin-login.html      # Admin login (not linked from nav)
│   ├── admin.html            # Admin queue, catalogue and invoices
│   └── assets/
│       ├── config.js         # Supabase URL + anon key, backend URL, Paystack public key
│       ├── app-data.js       # Supabase data layer + backend calls
│       └── styles.css        # "Kitenge bold" design system
├── backend/
│   ├── app.py                # Flask app
│   ├── paystack.py           # Paystack card payment endpoints
│   ├── requirements.txt
│   ├── runtime.txt           # python-3.11.7
│   └── Procfile              # web: python app.py
├── supabase/
│   ├── schema.sql            # Full schema: tables, RLS, RPCs, storage policy, demo seed
│   └── make_admin.sql        # Promote an existing account to admin
├── astro.config.mjs
├── package.json
└── vercel.json
```

## Local development

Prerequisites: Node.js 18+, Python 3.11, a Supabase project, and (for payments) Daraja sandbox credentials and a Paystack test account.

### 1. Database

In the Supabase dashboard, open **SQL Editor** and run `supabase/schema.sql` top to bottom. It is safe to run on a fresh project or re-run on an existing one, and creates the tables, RLS policies, RPCs, the private `creative-files` storage bucket and a demo station/slot seed. Then configure email confirmation under **Authentication**.

### 2. Frontend

In `public/assets/config.js` set:

- `SUPABASE_URL` and `SUPABASE_ANON_KEY`: your project's URL and **anon** key (Supabase → Project Settings → API).
- `BACKEND_URL`: the deployed backend, e.g. `https://rms-adsense-backend.onrender.com`, no trailing slash. Leave it empty to use `http://localhost:5000` when running locally.
- `PAYSTACK_PUBLIC_KEY`: your Paystack public key (`pk_test_…` or `pk_live_…`). Leave it empty to keep the Card option switched off.

Then:

```bash
npm install
npm run dev       # Astro dev server, http://localhost:4321
npm run build     # Static build into dist/
npm run preview   # Serve the build locally
```

### 3. Backend

Create `backend/.env` (it is git-ignored) with:

```
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
MPESA_CONSUMER_KEY=your_daraja_key
MPESA_CONSUMER_SECRET=your_daraja_secret
MPESA_SHORTCODE=174379
MPESA_PASSKEY=your_passkey
MPESA_BASE_URL=https://sandbox.safaricom.co.ke
MPESA_TRANSACTION_TYPE=CustomerPayBillOnline
MPESA_CALLBACK_URL=https://your-backend.onrender.com/mpesa-callback/<MPESA_CALLBACK_TOKEN>
MPESA_CALLBACK_TOKEN=long_random_string
SITE_URL=http://localhost:4321
PAYSTACK_SECRET_KEY=sk_test_...
# ALLOWED_ORIGINS=https://yoursite.com,https://www.yoursite.com   # optional, defaults to SITE_URL
# TRUSTED_PROXY_HOPS=1                                           # optional, reverse proxies in front (Render: 1)
```

`MPESA_BASE_URL` defaults to the sandbox and `MPESA_TRANSACTION_TYPE` to `CustomerPayBillOnline` if unset. `SITE_URL` is used to build the password-reset redirect (`<SITE_URL>/reset-password.html`) and is the default CORS origin. Generate `MPESA_CALLBACK_TOKEN` with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Without `PAYSTACK_SECRET_KEY` the card endpoints are disabled.

```bash
cd backend
pip install -r requirements.txt
python app.py     # http://localhost:5000 (or $PORT)
```

When the frontend is served from `localhost` and `BACKEND_URL` is empty, `app-data.js` calls the backend at `http://localhost:5000`.

## Backend API

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/mpesa-stk` | Supabase JWT (`Authorization: Bearer …`) | Send an STK push for an invoice the caller owns |
| POST | `/mpesa-callback/<token>` | Secret path token (called by Daraja) | Verify and mark the matching invoice paid |
| POST | `/password-reset` | None | Send a Supabase password-reset email |
| POST | `/paystack-initialize` | Supabase JWT | Start a card transaction for an invoice the caller owns |
| POST | `/paystack-verify` | Supabase JWT | Confirm a card transaction after the popup closes |
| POST | `/paystack-webhook` | Paystack signature (`x-paystack-signature`) | Paystack's charge notifications |
| GET | `/health` | None | Returns `{"status": "ok"}` |

**`POST /mpesa-stk`** — body `{"invoiceId": 123, "phone": "0712345678"}`. The phone is normalised to `2547…`. Rejects invoices that aren't the caller's or are already paid. Limited to 5 per user per 10 minutes. On success stores the `CheckoutRequestID` on the invoice and returns `{"ok": true, "checkoutRequestId": "ws_CO_…"}`.

**`POST /mpesa-callback/<token>`** — receives Daraja's `Body.stkCallback` payload. Daraja can't send a JWT, so the callback is never trusted on its own: the path token must match `MPESA_CALLBACK_TOKEN`, the `CheckoutRequestID` must belong to an unpaid invoice, the amount must match, and the backend re-checks the result with Daraja's STK status query before setting `payment_status = 'paid'`, `payment_method = 'mpesa'` and the receipt number. When `MPESA_CALLBACK_TOKEN` is set, the bare `/mpesa-callback` is refused. Rate limited per IP.

**`POST /password-reset`** — body `{"email": "user@example.com"}`. Limited to 3 requests per email and 10 per IP per hour (via the `check_rate_limit` RPC). Returns `{"success": true}` whether or not the account exists.

## Payment flow

### M-Pesa

1. The client opens an approved booking's invoice on `payment.html` and enters their phone number.
2. The frontend calls `POST /mpesa-stk` with the user's Supabase session token.
3. The backend gets a Daraja OAuth token and sends the STK push; the prompt appears on the phone.
4. The client enters their PIN; Daraja calls `POST /mpesa-callback`.
5. The backend marks the invoice paid, and the client sees it on their dashboard.

Admins can also mark an invoice paid manually from `admin.html` (the `mark_invoice_paid` RPC). Payments are only real once live Daraja credentials and the callback URL are configured; the frontend never fakes a successful payment.

### Card (Paystack)

1. On `payment.html` the client picks Card (shown only when `PAYSTACK_PUBLIC_KEY` is set).
2. The frontend calls `POST /paystack-initialize`; the backend creates the transaction for the invoice amount and returns an access code.
3. The Paystack popup collects the card, so card data never touches this site.
4. When the popup closes the frontend calls `POST /paystack-verify`; the backend checks the transaction with Paystack and marks the invoice paid with `payment_method = 'card'`.
5. `POST /paystack-webhook` does the same from Paystack's side, so a payment still lands if the browser closes early.

## Deployment

### Frontend (Vercel)

Connect the repo to Vercel (or run `vercel deploy`). `vercel.json` runs `npm run build` and serves `dist/`.

### Backend (Render)

Create a **Web Service** from this repo:

- **Root directory:** `backend`
- **Runtime:** Python (version from `runtime.txt`)
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python app.py` (matches the `Procfile`)
- **Environment:** every variable from the backend `.env` above, with `SITE_URL` set to the production frontend URL, `MPESA_CALLBACK_URL` set to `https://<your-service>.onrender.com/mpesa-callback/<MPESA_CALLBACK_TOKEN>` and `PAYSTACK_SECRET_KEY` set to your live or test secret key.

The app binds to Render's `$PORT` automatically. After deploying, register the callback URL with Safaricom, and in Paystack (Settings → API Keys & Webhooks) set the webhook URL to `https://<your-service>.onrender.com/paystack-webhook`.

**Backend URL in the frontend:** set `BACKEND_URL` in `public/assets/config.js` to the Render service URL. If it is empty on a non-localhost site, payments and password reset show a "backend is not configured" error.

## Admin setup

Admin status lives in the `public.admins` table and is checked through the `public.is_admin()` helper (SECURITY DEFINER), which every admin RLS policy uses; `profiles.role` is kept in sync by a trigger.

There is a single admin and no self-service admin signup. To set it up, register the account once through `login.html`, then edit the email in `supabase/make_admin.sql` and run it in the SQL Editor. The admin signs in at `admin-login.html`, which isn't linked from the site nav; reach it by direct URL. Once signed in, a switch beside the logo flips between the admin view and the client view of the site.

## Database

Everything is in `supabase/schema.sql`. Main tables:

- `profiles` — one row per auth user
- `admins` — admin membership
- `stations` — TV and radio stations
- `slots` — bookable airtime slots
- `bookings` — client booking requests
- `invoices` — amounts due, payment status and method, M-Pesa receipt, Paystack reference
- `rate_limits` — throttling state, used only via `check_rate_limit()`

Key RPCs: `submit_booking` (rate limited to 5 per user per 10 minutes), `approve_booking`, `reject_booking`, `admin_create_booking`, `mark_invoice_paid`.

Replace the demo station/slot seed with the real RMS catalogue before production.

## Security notes

- Only the Supabase **anon** key and the Paystack **public** key go in `config.js`. The service-role key, Daraja secrets and Paystack secret key live only in the backend's environment.
- All user-rendered data is HTML-escaped in `app-data.js`, and access control is enforced by Postgres RLS.
- Keep the `creative-files` bucket private.
- `/mpesa-callback` is guarded by the secret path token and re-verified with Daraja; `/paystack-webhook` checks Paystack's HMAC signature. Browser calls are only allowed from `ALLOWED_ORIGINS` (default `SITE_URL`).
- Serve everything over HTTPS.

## Design

`public/assets/styles.css` is the "kitenge bold" design system: burnt orange page canvas, deep teal chrome, gold accents and cream card surfaces; Archivo Black for headlines and prices, Archivo for body text, IBM Plex Mono for data; square corners throughout. The pages depend only on its class names and CSS variables (`.card`, `.badge-*`, `.pill-tab`, `--signal`, `--open`, …), so it can be swapped for another stylesheet that defines the same ones.
