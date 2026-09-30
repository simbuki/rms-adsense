// assets/config.js — fill in with your own Supabase project's values
// Find these in Supabase: Project Settings → API
// Never put the service_role key here — only the anon/public key.

const SUPABASE_URL = "https://yoflapaalwrwvqctxjrc.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlvZmxhcGFhbHdyd3ZxY3R4anJjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODc0OTEzOTUsImV4cCI6MjEwMzA2NzM5NX0.SX5LF569QCQlKLO9dsAoj6YbFFVs0C1AK9QkzEOu0Sw";

// Base URL of the deployed Python backend (backend/app.py), no trailing slash,
// e.g. "https://rms-adsense-backend.onrender.com". Leave empty to use
// http://localhost:5000 when running locally.
const BACKEND_URL = "";

// Paystack PUBLIC key for card payments (starts with pk_test_ or pk_live_).
// Find it in Paystack: Settings → API Keys & Webhooks. Leave empty to keep
// the Card option switched off. Never put the secret key (sk_...) here; it
// goes only in the backend's PAYSTACK_SECRET_KEY environment variable.
const PAYSTACK_PUBLIC_KEY = "pk_test_bc851f06c301902704f665b0c054e9c3ba962002";
