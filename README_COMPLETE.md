# RMS AdSense — Airtime Booking Marketplace

A modern, secure marketplace for booking TV and radio airtime slots across Royal Media Services. Built with Astro + Python backend + Supabase.

## Features

- **Self-serve booking** — Browse available slots, book with one click
- **Real M-Pesa integration** — Accept mobile money payments seamlessly
- **Admin dashboard** — Approve/reject requests, manage inventory, view revenue
- **Secure auth** — Supabase authentication with password reset
- **Rate limiting** — Prevent abuse (API level)
- **XSS protection** — All user input escaped before rendering
- **Row-level security** — Database-level access control
- **Kitenge bold design** — Distinctive burnt orange/teal/gold aesthetic

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Frontend | Astro | Static site generation + Supabase JS client |
| Backend | Python Flask | M-Pesa payment, callbacks, password reset |
| Database | Supabase | Managed PostgreSQL + Auth |
| Hosting | Vercel (frontend) + Render (backend) | Deployment |
| Payments | Daraja (M-Pesa) | Mobile money |

## Quick Start

### Prerequisites
- Node.js 18+
- Python 3.9+
- Supabase account
- Vercel account (for hosting)

### 1. Supabase Setup
```bash
# Install Supabase CLI
npm install -g supabase

# Link to your project
supabase login
supabase link --project-ref YOUR_PROJECT_REF

# Deploy database
supabase db push
```

### 2. Frontend (Local)
```bash
# Install dependencies
npm install

# Run dev server
npm run dev
# Opens http://localhost:3000
```

### 3. Backend (Local)
```bash
cd backend

# Install Python dependencies
pip install -r requirements.txt

# Create .env file
cp ../.env.example .env
# Edit .env with your credentials

# Run Flask
python app.py
# Opens http://localhost:5000
```

### 4. Test Flow
1. Sign up at http://localhost:3000/login.html
2. Browse slots at http://localhost:3000/browse.html
3. Make a booking
4. View dashboard at http://localhost:3000/dashboard.html

## Configuration

### Frontend: `public/assets/config.js`
```javascript
const SUPABASE_URL = "https://YOUR_PROJECT.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGci..."; // from Supabase dashboard
```

### Backend: `.env`
```
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
MPESA_CONSUMER_KEY=your_daraja_key
MPESA_CONSUMER_SECRET=your_daraja_secret
MPESA_SHORTCODE=174379
MPESA_PASSKEY=your_passkey
MPESA_BASE_URL=https://sandbox.safaricom.co.ke
MPESA_CALLBACK_URL=https://your-backend.onrender.com/mpesa-callback
SITE_URL=https://yoursite.com
```

## API Endpoints

### POST `/mpesa-stk`
Initiate M-Pesa payment prompt.

**Request** (requires JWT):
```json
{
  "invoiceId": 123,
  "phone": "0712345678"
}
```

**Response**:
```json
{
  "ok": true,
  "checkoutRequestId": "ws_co_abc..."
}
```

### POST `/mpesa-callback`
Receive payment confirmation from Daraja (no auth needed).

**Request** (from Daraja):
```json
{
  "Body": {
    "stkCallback": {
      "MerchantRequestID": "...",
      "CheckoutRequestID": "...",
      "ResultCode": 0,
      "ResultDesc": "The service request has been processed successfully",
      "CallbackMetadata": {
        "Item": [
          { "Name": "Amount", "Value": 5000 },
          { "Name": "MpesaReceiptNumber", "Value": "RJL..." },
          { "Name": "MpesaTransactionDate", "Value": "20240823..." }
        ]
      }
    }
  }
}
```

**Response**:
```json
{ "ok": true }
```

### POST `/password-reset`
Send password reset email.

**Request**:
```json
{ "email": "user@example.com" }
```

**Response**:
```json
{ "success": true }
```

### GET `/health`
Health check.

**Response**:
```json
{ "status": "ok" }
```

## Deployment

### Frontend (Vercel)
```bash
vercel deploy
```

Vercel auto-detects Astro config and builds.

### Backend (Render/Railway/Heroku)

**Render:**
1. Go to https://render.com
2. New Web Service
3. Connect GitHub repo
4. Runtime: Python 3.11
5. Build: `pip install -r backend/requirements.txt`
6. Start: `python backend/app.py`
7. Set environment variables
8. Deploy

**Railway:**
```bash
railway login
railway init
railway up
```

**Heroku:**
```bash
heroku create
heroku config:set MPESA_CONSUMER_KEY=... (all env vars)
git push heroku main
```

See **DEPLOYMENT_COMPLETE.md** for step-by-step guide.

## Security

All sensitive operations follow OWASP best practices:

1. **Input Validation** — Server-side checks on all user inputs
2. **Output Escaping** — HTML escaping on all rendered user data
3. **Rate Limiting** — 3-5 requests per 10 minutes per user per action
4. **Authentication** — Supabase JWT tokens on all protected endpoints
5. **Row-Level Security** — PostgreSQL RLS policies enforce access control
6. **Secrets Management** — Environment variables, never hardcoded

See **SECURITY.md** for complete rules.

## Pages

| Page | URL | Purpose |
|------|-----|---------|
| Home | `/` | Landing page |
| Sign Up | `/login.html` | Create client account |
| Browse | `/browse.html` | View & book slots |
| Dashboard | `/dashboard.html` | My bookings |
| Payment | `/payment.html` | Pay invoice |
| Admin Login | `/admin-login.html` | Admin sign in |
| Admin | `/admin.html` | Admin dashboard |
| Password Reset | `/reset-password.html` | New password |

## Database

All tables, RLS policies, and functions are in `supabase/schema.sql`. Deploy with:

```bash
supabase db push
```

Key tables:
- `auth.users` — Supabase auth (managed by Supabase)
- `public.stations` — TV/radio networks
- `public.slots` — Individual airtime slots
- `public.bookings` — Client booking requests
- `public.invoices` — Payments due
- `public.admins` — Admin user tracking
- `public.rate_limits` — Request throttling

## Project Structure

```
.
├── src/pages/
│   └── index.astro               # Home page (Astro)
├── public/
│   ├── assets/
│   │   ├── app-data.js           # Supabase client + API functions
│   │   ├── config.js             # Credentials
│   │   └── styles.css            # Kitenge design
│   └── *.html                    # All HTML pages
├── backend/
│   ├── app.py                    # Flask endpoints
│   └── requirements.txt          # Python deps
├── supabase/
│   └── schema.sql                # Database schema
├── astro.config.mjs              # Astro config
├── package.json                  # npm deps
├── vercel.json                   # Vercel config
└── .env.example                  # Environment template
```

## Development

### Commands
```bash
# Frontend
npm run dev       # Local development (http://localhost:3000)
npm run build     # Production build
npm run preview   # Preview build locally

# Backend
python backend/app.py             # Local server (http://localhost:5000)
```

### Testing
- **Unit**: Add tests in `src/tests/`
- **Integration**: Test against live Supabase (sandbox)
- **E2E**: Browser automation via Playwright/Cypress

### Debugging
- Frontend: Browser dev tools (F12)
- Backend: `flask run --debug` for auto-reload
- Database: Supabase Dashboard → SQL Editor

## M-Pesa Integration

M-Pesa payment flow:

1. User clicks "Pay" on payment.html
2. Frontend calls `POST /mpesa-stk` with invoice ID + phone
3. Backend gets Daraja OAuth token
4. Backend sends STK push request
5. M-Pesa prompt appears on user's phone
6. User enters PIN and confirms payment
7. Daraja sends callback to `POST /mpesa-callback`
8. Backend marks invoice as "paid"
9. Dashboard updates in real-time

See **DARAJA_SETUP.md** for sandbox configuration.

## Support & Contribution

- **Issues**: GitHub Issues
- **Docs**: See README_COMPLETE.md, SECURITY.md, DARAJA_SETUP.md
- **Contributing**: Fork, branch, PR

## License

MIT

---

**Built with ❤️ for Royal Media Services**

Latest update: August 2026
