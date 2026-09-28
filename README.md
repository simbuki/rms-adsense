# RMS AdSense — Airtime Booking Marketplace

A self-serve marketplace for booking TV and radio airtime slots across Royal Media Services. Clients browse the slot catalogue, request bookings, upload creatives and pay invoices by M-Pesa; admins approve bookings, manage the catalogue and track invoices.

## Tech stack

| Layer | Technology | Where it runs |
|-------|-----------|---------------|
| Frontend | Static HTML/JS in `public/`, built with Astro | Vercel |
| Data + auth | Supabase (Postgres, Auth, Storage, RLS) | Supabase |
| Backend | Python Flask (`backend/app.py`): M-Pesa STK push, M-Pesa callback, password reset | Render |
| Payments | Safaricom Daraja (M-Pesa STK push) | — |

The browser talks to Supabase directly (with the anon key and row-level security) for everything except the operations that need secrets: M-Pesa and password-reset emails go through the Flask backend, which holds the Supabase service-role key and Daraja credentials.

## Project structure

```
.
├── public/                   # Served as-is by Astro
│   ├── index.html            # Landing page
│   ├── login.html            # Client login / registration
│   ├── browse.html           # Slot catalogue, booking, creative upload
│   ├── dashboard.html        # Client's bookings and invoices
│   ├── payment.html          # M-Pesa payment UI
│   ├── reset-password.html   # Set a new password from the reset email link
│   ├── admin-login.html      # Admin login (not linked from nav)
│   ├── admin.html            # Admin queue, catalogue and invoices
│   └── assets/
│       ├── config.js         # Supabase URL + anon key
│       ├── app-data.js       # Supabase data layer + backend calls
│       └── styles.css        # "Kitenge bold" design system
├── src/pages/index.astro     # Astro home page
├── backend/
│   ├── app.py                # Flask app
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

Prerequisites: Node.js 18+, Python 3.11, a Supabase project, and (for payments) Daraja sandbox credentials.

### 1. Database

In the Supabase dashboard, open **SQL Editor** and run `supabase/schema.sql` top to bottom. It is safe to run on a fresh project and creates the tables, RLS policies, RPCs, the private `creative-files` storage bucket and a demo station/slot seed. Then configure email confirmation under **Authentication**.

### 2. Frontend

Set your project's URL and **anon** key in `public/assets/config.js` (Supabase → Project Settings → API). Then:

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
MPESA_CALLBACK_URL=https://your-backend.onrender.com/mpesa-callback
SITE_URL=http://localhost:4321
```

`MPESA_BASE_URL` defaults to the sandbox and `MPESA_TRANSACTION_TYPE` to `CustomerPayBillOnline` if unset. `SITE_URL` is used to build the password-reset redirect (`<SITE_URL>/reset-password.html`).

```bash
cd backend
pip install -r requirements.txt
python app.py     # http://localhost:5000 (or $PORT)
```

When the frontend is served from `localhost`, `app-data.js` calls the backend at `http://localhost:5000`.

## Backend API

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/mpesa-stk` | Supabase JWT (`Authorization: Bearer …`) | Send an STK push for an invoice the caller owns |
| POST | `/mpesa-callback` | None (called by Daraja) | Mark the matching invoice paid |
| POST | `/password-reset` | None | Send a Supabase password-reset email |
| GET | `/health` | None | Returns `{"status": "ok"}` |

**`POST /mpesa-stk`** — body `{"invoiceId": 123, "phone": "0712345678"}`. The phone is normalised to `2547…`. Rejects invoices that aren't the caller's or are already paid. On success stores the `CheckoutRequestID` on the invoice and returns `{"ok": true, "checkoutRequestId": "ws_CO_…"}`.

**`POST /mpesa-callback`** — receives Daraja's `Body.stkCallback` payload. When `ResultCode` is `0`, the invoice with that `CheckoutRequestID` is updated to `payment_status = 'paid'`, `payment_method = 'mpesa'` and the `MpesaReceiptNumber`. Always responds `{"ok": true}`.

**`POST /password-reset`** — body `{"email": "user@example.com"}`. Limited to 3 requests per email per hour (via the `check_rate_limit` RPC). Returns `{"success": true}` whether or not the account exists.

## Payment flow

1. The client opens an approved booking's invoice on `payment.html` and enters their phone number.
2. The frontend calls `POST /mpesa-stk` with the user's Supabase session token.
3. The backend gets a Daraja OAuth token and sends the STK push; the prompt appears on the phone.
4. The client enters their PIN; Daraja calls `POST /mpesa-callback`.
5. The backend marks the invoice paid, and the client sees it on their dashboard.

Admins can also mark an invoice paid manually from `admin.html` (the `mark_invoice_paid` RPC). Payments are only real once live Daraja credentials and the callback URL are configured; the frontend never fakes a successful payment.

Card payments are intentionally disabled: `payment.html` shows the Card tab but no card data is collected. Connect a PCI-compliant provider through the backend before enabling it.

## Deployment

### Frontend (Vercel)

Connect the repo to Vercel (or run `vercel deploy`). `vercel.json` runs `npm run build` and serves `dist/`.

### Backend (Render)

Create a **Web Service** from this repo:

- **Root directory:** `backend`
- **Runtime:** Python (version from `runtime.txt`)
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python app.py` (matches the `Procfile`)
- **Environment:** every variable from the backend `.env` above, with `SITE_URL` set to the production frontend URL and `MPESA_CALLBACK_URL` set to `https://<your-service>.onrender.com/mpesa-callback`.

The app binds to Render's `$PORT` automatically. After deploying, register the callback URL with Safaricom.

**Backend URL in the frontend:** `app-data.js` currently derives the production backend URL from the site's hostname as `https://<hostname>-backend.onrender.com`. If your Render service has a different URL, update `BACKEND_URL` in `public/assets/app-data.js`.

## Admin setup

Admin status lives in the `public.admins` table and is checked through the `public.is_admin()` helper (SECURITY DEFINER), which every admin RLS policy uses; `profiles.role` is kept in sync by a trigger.

There is a single admin and no self-service admin signup. To set it up, register the account once through `login.html`, then edit the email in `supabase/make_admin.sql` and run it in the SQL Editor. The admin signs in at `admin-login.html`, which isn't linked from the site nav; reach it by direct URL.

## Database

Everything is in `supabase/schema.sql`. Main tables:

- `profiles` — one row per auth user
- `admins` — admin membership
- `stations` — TV and radio stations
- `slots` — bookable airtime slots
- `bookings` — client booking requests
- `invoices` — amounts due, payment status, M-Pesa receipt
- `rate_limits` — throttling state, used only via `check_rate_limit()`

Key RPCs: `submit_booking` (rate limited to 5 per user per 10 minutes), `approve_booking`, `reject_booking`, `admin_create_booking`, `mark_invoice_paid`.

Replace the demo station/slot seed with the real RMS catalogue before production.

## Security notes

- Only the Supabase **anon** key goes in `config.js`. The service-role key and Daraja secrets live only in the backend's environment.
- All user-rendered data is HTML-escaped in `app-data.js`, and access control is enforced by Postgres RLS.
- Keep the `creative-files` bucket private.
- `/mpesa-callback` is unauthenticated and does not yet verify the request came from Safaricom, and the backend allows CORS from any origin. Tighten both before going live.
- Serve everything over HTTPS.

## Design

`public/assets/styles.css` is the "kitenge bold" design system: burnt orange page canvas, deep teal chrome, gold accents and cream card surfaces; Archivo Black for headlines and prices, Archivo for body text, IBM Plex Mono for data; square corners throughout. The pages depend only on its class names and CSS variables (`.card`, `.badge-*`, `.pill-tab`, `--signal`, `--open`, …), so it can be swapped for another stylesheet that defines the same ones.
