"""
RMS AdSense Backend — All Edge Functions Combined

Three endpoints in one Flask app:
1. POST /mpesa-stk — Initiate M-Pesa STK push for payment
2. POST /mpesa-callback — Receive M-Pesa payment confirmation
3. POST /password-reset — Send password reset email

Deploy to Render, Railway, Heroku, or any Python host.

Required environment variables:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET, MPESA_SHORTCODE, MPESA_PASSKEY
  MPESA_BASE_URL, MPESA_TRANSACTION_TYPE, MPESA_CALLBACK_URL
  SITE_URL

Optional environment variables:
  MPESA_CALLBACK_TOKEN  Secret path segment for the callback. When set, Daraja
                        must call /mpesa-callback/<token> (put the token in
                        MPESA_CALLBACK_URL) and the bare /mpesa-callback is refused.
  ALLOWED_ORIGINS       Comma-separated browser origins allowed by CORS.
                        Defaults to SITE_URL.
  TRUSTED_PROXY_HOPS    Reverse proxies in front of the app (default 1, e.g. Render).
"""

import os
import json
import base64
import hmac
from datetime import datetime
from functools import wraps

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ============================================================================
# Configuration
# ============================================================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# M-Pesa (Daraja)
MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET")
MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE")
MPESA_PASSKEY = os.getenv("MPESA_PASSKEY")
MPESA_BASE_URL = os.getenv("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke")
MPESA_TRANSACTION_TYPE = os.getenv("MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline")
MPESA_CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL")

MPESA_CALLBACK_TOKEN = os.getenv("MPESA_CALLBACK_TOKEN", "")

# Site
SITE_URL = os.getenv("SITE_URL")

# Trust X-Forwarded-For only from our own proxy hop(s), so request.remote_addr
# is the real client IP and can't be spoofed by a client-supplied header.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=int(os.getenv("TRUSTED_PROXY_HOPS", "1")))

# CORS: only the site itself may call the API from a browser. The M-Pesa
# callback is server-to-server and needs no CORS at all.
ALLOWED_ORIGINS = [
    o.strip().rstrip("/")
    for o in (os.getenv("ALLOWED_ORIGINS") or SITE_URL or "").split(",")
    if o.strip()
]
if not ALLOWED_ORIGINS:
    print("WARNING: neither ALLOWED_ORIGINS nor SITE_URL is set; browser calls will be blocked by CORS")
CORS(
    app,
    resources={r"/(mpesa-stk|password-reset)": {"origins": ALLOWED_ORIGINS}},
    allow_headers=["Authorization", "Content-Type"],
    methods=["POST", "OPTIONS"],
)


def rate_limited(key, max_count, window_seconds):
    """True if `key` has exceeded max_count within window_seconds (shared across instances via Supabase)."""
    result = supabase.rpc("check_rate_limit", {
        "p_key": key,
        "p_max_count": max_count,
        "p_window_seconds": window_seconds,
    }).execute()
    return not result.data


def require_auth(f):
    """Decorator: require Authorization header with valid JWT"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # CORS preflights never carry credentials; let them through.
        if request.method == "OPTIONS":
            return "", 204

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return (
                jsonify({"error": "Not authenticated"}),
                401,
            )
        
        jwt_token = auth_header.replace("Bearer ", "")
        
        # Verify JWT with Supabase
        try:
            user = supabase.auth.get_user(jwt_token)
            if not user or not user.user:
                return (
                    jsonify({"error": "Invalid token"}),
                    401,
                )
            request.user = user.user
        except Exception as e:
            return (
                jsonify({"error": f"Auth failed: {str(e)}"}),
                401,
            )
        
        return f(*args, **kwargs)
    
    return decorated_function


def get_mpesa_auth_token():
    """Get OAuth2 token from Daraja"""
    auth_string = f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}"
    auth_bytes = auth_string.encode("utf-8")
    auth_base64 = base64.b64encode(auth_bytes).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {auth_base64}",
        "Content-Type": "application/json",
    }
    
    url = f"{MPESA_BASE_URL}/oauth/v1/generate?grant_type=client_credentials"
    response = requests.get(url, headers=headers, timeout=10)
    
    if response.status_code != 200:
        raise Exception(f"Daraja auth failed: {response.text}")
    
    data = response.json()
    return data.get("access_token")


def get_mpesa_timestamp():
    """Return timestamp in yyyymmddhhmmss format"""
    return datetime.now().strftime("%Y%m%d%H%M%S")


def get_mpesa_password(timestamp):
    """Daraja password: base64(shortcode + passkey + timestamp)"""
    return base64.b64encode(f"{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}".encode()).decode()


def query_stk_status(checkout_request_id):
    """
    Ask Daraja directly for the result of an STK push.

    The callback endpoint is public, so its payload can't be trusted on its
    own. This query goes out over our own authenticated connection to
    Safaricom, so a successful answer here is the real proof of payment.
    Returns the response dict, or None if the query itself failed.
    """
    access_token = get_mpesa_auth_token()
    timestamp = get_mpesa_timestamp()
    payload = {
        "BusinessShortCode": MPESA_SHORTCODE,
        "Password": get_mpesa_password(timestamp),
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    url = f"{MPESA_BASE_URL}/mpesa/stkpushquery/v1/query"
    response = requests.post(url, json=payload, headers=headers, timeout=15)
    if response.status_code != 200:
        print(f"STK query failed for {checkout_request_id}: {response.status_code} {response.text}")
        return None
    return response.json()


def normalize_phone(phone):
    """Convert phone to 254XXXXXXXXX format"""
    digits = "".join(c for c in phone if c.isdigit())
    
    if digits.startswith("254"):
        return digits
    if digits.startswith("0"):
        return "254" + digits[1:]
    if digits.startswith("7") or digits.startswith("1"):
        return "254" + digits
    
    return digits


# ============================================================================
# Endpoints
# ============================================================================

@app.route("/mpesa-stk", methods=["OPTIONS", "POST"])
@require_auth
def mpesa_stk():
    """
    Initiate M-Pesa STK push.
    
    POST body: { "invoiceId": 123, "phone": "0712345678" }
    """
    if request.method == "OPTIONS":
        return "", 204
    
    try:
        if rate_limited(f"mpesa_stk:{request.user.id}", 5, 600):
            return jsonify({"error": "Too many payment attempts. Please wait a few minutes and try again"}), 429

        body = request.get_json() or {}
        invoice_id = body.get("invoiceId")
        phone = body.get("phone")
        
        if not invoice_id or not phone:
            return jsonify({"error": "invoiceId and phone required"}), 400
        
        # Fetch invoice
        invoice_result = supabase.table("invoices").select(
            "id, amount, payment_status, booking_id, bookings(client_id)"
        ).eq("id", invoice_id).execute()
        
        if not invoice_result.data or len(invoice_result.data) == 0:
            return jsonify({"error": "Invoice not found"}), 404
        
        invoice_data = invoice_result.data[0]
        booking = invoice_data.get("bookings", {})
        booking_client_id = booking.get("client_id") if isinstance(booking, dict) else None
        
        # Verify ownership
        if booking_client_id != request.user.id:
            return jsonify({"error": "This invoice does not belong to you"}), 403
        
        if invoice_data.get("payment_status") == "paid":
            return jsonify({"error": "Invoice already paid"}), 400
        
        # Get M-Pesa token
        access_token = get_mpesa_auth_token()
        
        # Prepare STK push
        timestamp = get_mpesa_timestamp()
        password = get_mpesa_password(timestamp)
        msisdn = normalize_phone(phone)
        
        stk_payload = {
            "BusinessShortCode": MPESA_SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": MPESA_TRANSACTION_TYPE,
            "Amount": int(invoice_data.get("amount", 0)),
            "PartyA": msisdn,
            "PartyB": MPESA_SHORTCODE,
            "PhoneNumber": msisdn,
            "CallBackURL": MPESA_CALLBACK_URL,
            "AccountReference": f"INV-{invoice_id}",
            "TransactionDesc": "RMS AdSense airtime booking",
        }
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        
        stk_url = f"{MPESA_BASE_URL}/mpesa/stkpush/v1/processrequest"
        stk_response = requests.post(stk_url, json=stk_payload, headers=headers, timeout=30)
        
        if stk_response.status_code != 200:
            return jsonify({"error": f"STK push failed: {stk_response.text}"}), 502
        
        stk_data = stk_response.json()
        
        if stk_data.get("ResponseCode") != "0":
            return jsonify({"error": stk_data.get("errorMessage", "STK push failed")}), 502
        
        # Store checkout request ID
        checkout_request_id = stk_data.get("CheckoutRequestID")
        supabase.table("invoices").update({
            "mpesa_checkout_request_id": checkout_request_id
        }).eq("id", invoice_id).execute()
        
        return jsonify({
            "ok": True,
            "checkoutRequestId": checkout_request_id,
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _callback_item(items, name):
    for item in items or []:
        if isinstance(item, dict) and item.get("Name") == name:
            return item.get("Value")
    return None


@app.route("/mpesa-callback", methods=["POST"])
@app.route("/mpesa-callback/<token>", methods=["POST"])
def mpesa_callback(token=None):
    """
    Receive M-Pesa payment confirmation from Daraja.

    Daraja can't send a JWT, so this endpoint is public. A callback is never
    trusted on its own; before an invoice is marked paid:
      1. the secret path token must match (when MPESA_CALLBACK_TOKEN is set),
      2. the CheckoutRequestID must belong to an unpaid invoice,
      3. the reported amount must match the invoice amount,
      4. Daraja's STK status query must independently confirm success.

    Always answers 200 with Daraja's expected ack so Safaricom doesn't retry
    forged or irrelevant requests; rejections are logged instead.
    """
    ack = jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})

    if MPESA_CALLBACK_TOKEN and not hmac.compare_digest(token or "", MPESA_CALLBACK_TOKEN):
        print(f"Callback rejected: bad or missing token from {request.remote_addr}")
        return jsonify({"error": "Not found"}), 404

    try:
        if rate_limited(f"mpesa_callback:{request.remote_addr}", 60, 60):
            print(f"Callback rejected: rate limited {request.remote_addr}")
            return jsonify({"error": "Too many requests"}), 429

        body = request.get_json(silent=True) or {}
        stk_callback = (body.get("Body") or {}).get("stkCallback") or {}
        checkout_request_id = stk_callback.get("CheckoutRequestID")

        if not isinstance(checkout_request_id, str) or not checkout_request_id:
            return ack, 200

        # Failed or cancelled payments change nothing.
        if str(stk_callback.get("ResultCode")) != "0":
            return ack, 200

        invoice_result = supabase.table("invoices").select(
            "id, amount, payment_status"
        ).eq("mpesa_checkout_request_id", checkout_request_id).execute()

        if not invoice_result.data:
            print(f"Callback rejected: unknown CheckoutRequestID {checkout_request_id}")
            return ack, 200

        invoice = invoice_result.data[0]
        if invoice.get("payment_status") == "paid":
            return ack, 200

        items = (stk_callback.get("CallbackMetadata") or {}).get("Item") or []
        amount = _callback_item(items, "Amount")
        receipt = _callback_item(items, "MpesaReceiptNumber")

        try:
            amount_ok = int(float(amount)) == int(invoice.get("amount", 0))
        except (TypeError, ValueError):
            amount_ok = False
        if not amount_ok or not receipt:
            print(f"Callback rejected: invoice {invoice['id']} amount/receipt mismatch ({amount!r}, {receipt!r})")
            return ack, 200

        status = query_stk_status(checkout_request_id)
        if not status or str(status.get("ResultCode")) != "0":
            print(f"Callback rejected: Daraja did not confirm {checkout_request_id}: {status}")
            return ack, 200

        # Mark invoice as paid (only if still unpaid, so replays are no-ops)
        supabase.table("invoices").update({
            "payment_status": "paid",
            "payment_method": "mpesa",
            "mpesa_receipt": str(receipt),
        }).eq("id", invoice["id"]).eq("payment_status", "unpaid").execute()

        return ack, 200

    except Exception as e:
        print(f"Callback error: {str(e)}")
        return ack, 200


@app.route("/password-reset", methods=["OPTIONS", "POST"])
def password_reset():
    """
    Send password reset email.
    Rate limited: 3 per email and 10 per IP per hour.
    """
    if request.method == "OPTIONS":
        return "", 204
    
    try:
        body = request.get_json() or {}
        email = (body.get("email") or "").strip()
        
        if not email:
            return jsonify({"error": "Email is required"}), 400
        
        # Validate email
        if "@" not in email or "." not in email.split("@")[-1]:
            return jsonify({"error": "Enter a valid email address"}), 400
        
        # Rate limit per email and per client IP
        if (rate_limited(f"password_reset:{email.lower()}", 3, 3600)
                or rate_limited(f"password_reset_ip:{request.remote_addr}", 10, 3600)):
            return jsonify({
                "error": "Too many password reset requests. Please try again later"
            }), 429
        
        # Send reset link via Supabase Auth
        supabase.auth.reset_password_for_email(
            email,
            {"redirect_to": f"{SITE_URL.rstrip('/')}/reset-password.html"}
        )
        
        # Always return success (don't leak email existence)
        return jsonify({"success": True}), 200
    
    except Exception as e:
        # Always return success for security
        return jsonify({"success": True}), 200


@app.route("/health", methods=["GET"])
def health():
    """Health check"""
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
