// assets/app-data.js — Supabase data layer for RMS AdSense
// Depends on assets/config.js (SUPABASE_URL, SUPABASE_ANON_KEY) and the
// Supabase JS client, both loaded by <script> tags before this file.

const sb = supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// Python backend URL (set this to your deployed Python backend)
const BACKEND_URL = window.location.hostname === 'localhost' 
  ? 'http://localhost:5000'
  : `https://${window.location.hostname.replace('www.', '')}-backend.onrender.com`;

let _stationsCache = null;
let _slotsCache = null;

/* ---------------- Security: HTML escaping ---------------- */

// Every field a client can type into a booking form (business name, ad
// description, phone, etc.) ends up rendered via innerHTML in admin.html,
// dashboard.html, browse.html, and payment.html. Without escaping, a
// business name like `<img src=x onerror=...>` would execute arbitrary
// JS in whoever's browser renders it — most dangerously, an admin's
// authenticated session when they open the pending queue. ALWAYS wrap
// any user-supplied string with this before interpolating it into an
// innerHTML template literal.
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ---------------- Formatting helpers ---------------- */

function KES(amount) {
  return "KES " + Number(amount).toLocaleString("en-KE");
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

const STATUS_META = {
  open: { label: "Open", cls: "badge-open" },
  pending: { label: "Pending", cls: "badge-pending" },
  booked: { label: "Booked", cls: "badge-booked" },
  approved: { label: "Approved", cls: "badge-approved" },
  rejected: { label: "Rejected", cls: "badge-rejected" },
  unpaid: { label: "Unpaid", cls: "badge-unpaid" },
  paid: { label: "Paid", cls: "badge-paid" },
};

function badgeHtml(status) {
  const meta = STATUS_META[status] || { label: status, cls: "badge-open" };
  return `<span class="badge ${meta.cls}">${meta.label}</span>`;
}

/* ---------------- Session / auth ---------------- */

async function getSession() {
  const { data: { session } } = await sb.auth.getSession();
  if (!session) return null;
  const { data: profile, error } = await sb
    .from("profiles")
    .select("*")
    .eq("id", session.user.id)
    .single();
  if (error || !profile) return null;
  return {
    id: session.user.id,
    email: profile.email,
    role: profile.role,
    name: profile.business_name,
    businessType: profile.business_type,
    county: profile.county,
    phone: profile.phone,
  };
}

// Redirects away if there's no session or the wrong role. Returns the
// session (or null — caller should stop rendering when null comes back,
// a redirect is already underway).
async function requireRole(role) {
  const session = await getSession();
  if (!session) {
    window.location.href = "login.html";
    return null;
  }
  if (session.role !== role) {
    window.location.href = session.role === "admin" ? "admin.html" : "browse.html";
    return null;
  }
  return session;
}

async function login(email, password) {
  const { data, error } = await sb.auth.signInWithPassword({ email, password });
  if (error) {
    if (error.message && error.message.toLowerCase().includes("email not confirmed")) {
      return { error: "Please confirm your email before logging in — check your inbox for the confirmation link." };
    }
    return { error: "Incorrect email or password." };
  }
  if (!data.session || !data.user) return { error: "Incorrect email or password." };

  // Use the user object signInWithPassword() just handed back directly,
  // rather than re-calling getSession() — right after sign-in, a second
  // getSession() call can occasionally race the client's internal session
  // sync and come back empty even though sign-in genuinely succeeded.
  const { data: profile, error: profileError } = await sb
    .from("profiles")
    .select("*")
    .eq("id", data.user.id)
    .single();

  if (profileError || !profile) {
    return { error: "Logged in, but your profile could not be loaded. Try again." };
  }

  return {
    id: data.user.id,
    email: profile.email,
    role: profile.role,
    name: profile.business_name,
    businessType: profile.business_type,
    county: profile.county,
    phone: profile.phone,
  };
}

async function register(formData) {
  const { data, error } = await sb.auth.signUp({
    email: formData.email,
    password: formData.password,
    options: {
      data: {
        business_name: formData.businessName,
        business_type: formData.businessType,
        county: formData.county,
        phone: formData.phone,
      },
    },
  });
  if (error) return { error: error.message };
  if (!data.session) {
    return { error: "Check your email to confirm your account, then log in." };
  }
  const session = await getSession();
  return session || { error: "Account created, but the profile could not be loaded. Try logging in." };
}

// Admin self-serve signup: creates the auth user, then redeems a
// single-use invite code (see supabase/add_admin_invite_flow.sql) to
// grant admin access. The invite code is required server-side — this
// function alone cannot make someone an admin without a valid code.
async function registerAdmin(email, password, inviteCode) {
  const { data, error } = await sb.auth.signUp({ email, password });
  if (error) return { error: error.message };
  if (!data.session) {
    return { error: "Check your email to confirm your account, then log in with the admin login page and it will pick up your invite automatically on next attempt." };
  }

  const { error: claimError } = await sb.rpc("claim_admin_invite", { p_code: inviteCode });
  if (claimError) {
    return { error: claimError.message || "That invite code isn't valid or has already been used." };
  }

  const session = await getSession();
  return session || { error: "Account created, but the profile could not be loaded. Try logging in." };
}

async function generateAdminInvite() {
  const { data, error } = await sb.rpc("generate_admin_invite");
  if (error) throw error;
  return data;
}

async function logout() {
  await sb.auth.signOut();
  window.location.href = "login.html";
}

// Sends a password-reset email via Python backend
async function requestPasswordReset(email) {
  try {
    const response = await fetch(`${BACKEND_URL}/password-reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    
    const data = await response.json();
    
    if (!response.ok) {
      return { error: data.error || "Could not send reset email" };
    }
    
    return { success: true };
  } catch (err) {
    return { error: err.message || "Could not send reset email" };
  }
}

// Called from reset-password.html once Supabase has established a
// recovery session from the emailed link.
async function updatePassword(newPassword) {
  const { error } = await sb.auth.updateUser({ password: newPassword });
  if (error) return { error: error.message };
  return { success: true };
}

/* ---------------- Read data ---------------- */

async function getStations() {
  if (_stationsCache) return _stationsCache;
  const { data, error } = await sb.from("stations").select("*").order("id");
  _stationsCache = error ? [] : data;
  return _stationsCache;
}

async function getSlots() {
  const { data, error } = await sb.from("slots").select("*").order("id");
  _slotsCache = error ? [] : data;
  return _slotsCache;
}

// Sync lookups — call getStations()/getSlots() first so the cache is warm.
function stationById(id) {
  return (_stationsCache || []).find((s) => s.id === id) || { name: "—", type: "TV", note: "" };
}
function slotById(id) {
  return (_slotsCache || []).find((s) => s.id === id) || null;
}

// Admin-only (enforced by RLS): catalogue management.
async function createStation(form) {
  const { data, error } = await sb.from("stations").insert({
    name: form.name, type: form.type, note: form.note || "",
  }).select().single();
  if (error) throw error;
  _stationsCache = null;
  return data;
}

async function createSlot(form) {
  const { data, error } = await sb.from("slots").insert({
    station_id: form.stationId, label: form.label, day: form.day, time: form.time,
    duration: form.duration, price: form.price, tier: form.tier || "standard", status: "open",
  }).select().single();
  if (error) throw error;
  return data;
}

async function updateSlot(slotId, form) {
  const { data, error } = await sb.from("slots").update({
    label: form.label, day: form.day, time: form.time,
    duration: form.duration, price: form.price, tier: form.tier, status: form.status,
  }).eq("id", slotId).select().single();
  if (error) throw error;
  return data;
}

async function deleteSlot(slotId) {
  const { error } = await sb.from("slots").delete().eq("id", slotId);
  if (error) throw error;
}

function mapBookingRow(b) {
  return {
    id: b.id,
    slotId: b.slot_id,
    businessName: b.business_name,
    businessType: b.business_type,
    county: b.county,
    email: b.email,
    phone: b.phone,
    adDescription: b.ad_description,
    fileName: b.file_name,
    filePath: b.file_path,
    status: b.status,
    submittedAt: b.submitted_at,
  };
}

// RLS scopes this automatically: clients see only their own bookings,
// admins see everything.
async function getBookings() {
  const { data, error } = await sb
    .from("bookings")
    .select("*")
    .order("submitted_at", { ascending: false });
  return error ? [] : data.map(mapBookingRow);
}

// RLS scopes this too: clients see invoices for their own bookings only.
async function getInvoices() {
  const { data, error } = await sb.from("invoices").select("*").order("id");
  if (error) return [];
  return data.map((inv) => ({
    id: inv.id,
    bookingId: inv.booking_id,
    invoiceNo: inv.invoice_no,
    amount: inv.amount,
    issuedDate: inv.issued_date,
    paymentStatus: inv.payment_status,
    paymentMethod: inv.payment_method,
    markedPaidAt: inv.marked_paid_at,
  }));
}

// Admin-only: manually confirm payment for cash/bank-transfer invoices
// that didn't come through the M-Pesa flow.
async function markInvoicePaid(invoiceId) {
  const { data, error } = await sb.rpc("mark_invoice_paid", { p_invoice_id: invoiceId });
  if (error) throw error;
  return data;
}

/* ---------------- Write data ---------------- */

async function uploadCreative(file, userId) {
  if (!file) return { fileName: "", filePath: null };
  const path = `${userId}/${Date.now()}-${file.name}`;
  const { error } = await sb.storage.from("creative-files").upload(path, file);
  if (error) throw error;
  return { fileName: file.name, filePath: path };
}

async function submitBooking(slotId, form, file, userId) {
  const { fileName, filePath } = await uploadCreative(file, userId);
  const { data, error } = await sb.rpc("submit_booking", {
    p_slot_id: slotId,
    p_business_name: form.businessName,
    p_business_type: form.businessType,
    p_county: form.county,
    p_email: form.email,
    p_phone: form.phone,
    p_ad_description: form.adDescription,
    p_file_name: fileName,
    p_file_path: filePath,
  });
  if (error) throw error;
  return mapBookingRow(data);
}

async function approveBooking(bookingId) {
  const { data, error } = await sb.rpc("approve_booking", { p_booking_id: bookingId });
  if (error) throw error;
  return data;
}

async function rejectBooking(bookingId) {
  const { data, error } = await sb.rpc("reject_booking", { p_booking_id: bookingId });
  if (error) throw error;
  return data;
}

// Kicks off an M-Pesa STK push via the mpesa-stk edge function. Card
// payments are intentionally not wired up — see README.
// Admin-only: creates and auto-approves a booking directly (phone-in
// or walk-in clients). Returns the invoice issued for it immediately.
async function adminCreateBooking(slotId, form) {
  const { data, error } = await sb.rpc("admin_create_booking", {
    p_slot_id: slotId,
    p_business_name: form.businessName,
    p_business_type: form.businessType,
    p_county: form.county,
    p_email: form.email,
    p_phone: form.phone,
    p_ad_description: form.adDescription,
  });
  if (error) throw error;
  return data;
}

// Admin-only: generates a fresh single-use invite code and emails it
// directly to the given address via the invite-admin edge function.
// The edge function's own RPC call re-checks admin status server-side,
// so this can't be abused even if called with a forged request.
async function payWithMpesa(invoiceId, phone) {
  const { data } = await sb.auth.getSession();
  const token = data.session?.access_token;
  
  if (!token) throw new Error("Not authenticated");
  
  const response = await fetch(`${BACKEND_URL}/mpesa-stk`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`,
    },
    body: JSON.stringify({ invoiceId, phone }),
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || "Payment failed");
  }
  
  return await response.json();
}

/* ---------------- Chrome: nav / footer / ticker ---------------- */

async function renderNav(active) {
  const root = document.getElementById("nav-root");
  if (!root) return;
  const session = await getSession();
  const onDark = active === "home";

  let links;
  if (!session) {
    links = `<a class="nav-link" href="login.html">Log in</a>
             <a class="btn btn-signal btn-sm" href="login.html?tab=register">Get started</a>`;
  } else if (session.role === "admin") {
    links = `<a class="nav-link${active === "admin" ? " active" : ""}" href="admin.html">Admin</a>
             <button type="button" class="nav-link" id="logout-btn">Log out</button>`;
  } else {
    links = `<a class="nav-link${active === "browse" ? " active" : ""}" href="browse.html">Browse</a>
             <a class="nav-link${active === "dashboard" ? " active" : ""}" href="dashboard.html">Dashboard</a>
             <button type="button" class="nav-link" id="logout-btn">Log out</button>`;
  }

  root.innerHTML = `
    <nav class="site-nav${onDark ? " on-dark" : ""}">
      <a class="brand" href="index.html">RMS <span>AdSense</span></a>
      <div class="links">${links}</div>
    </nav>`;

  const logoutBtn = document.getElementById("logout-btn");
  if (logoutBtn) logoutBtn.addEventListener("click", logout);
}

function renderFooter(rootId) {
  const root = document.getElementById(rootId);
  if (!root) return;
  root.innerHTML = `
    <footer class="site-footer">
      <span>© ${new Date().getFullYear()} RMS AdSense — Royal Media Services</span>
      <span>A self-service marketplace for TV &amp; radio airtime</span>
    </footer>`;
}

async function renderTicker(rootId) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const stations = await getStations();
  const items = stations.map((s) => `${s.name} · ${s.type}`).join("   —   ") || "Loading stations…";
  root.innerHTML = `<div class="ticker-track">${items}   —   ${items}</div>`;
}