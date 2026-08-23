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
"""

import os
import json
import base64
import hashlib
from datetime import datetime
from functools import wraps

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

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

# Site
SITE_URL = os.getenv("SITE_URL")


def cors_headers():
    """Return CORS headers"""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "authorization, content-type",
        "Content-Type": "application/json",
    }


def require_auth(f):
    """Decorator: require Authorization header with valid JWT"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
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
        password_string = f"{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}"
        password = base64.b64encode(password_string.encode()).decode()
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


@app.route("/mpesa-callback", methods=["OPTIONS", "POST"])
def mpesa_callback():
    """
    Receive M-Pesa payment confirmation from Daraja.
    No authentication (Daraja cannot send JWT).
    """
    if request.method == "OPTIONS":
        return "", 204
    
    try:
        body = request.get_json() or {}
        stk_callback = body.get("Body", {}).get("stkCallback", {})
        
        if not stk_callback:
            return jsonify({"ok": True}), 200
        
        result_code = stk_callback.get("ResultCode")
        checkout_request_id = stk_callback.get("CheckoutRequestID")
        
        # Payment succeeded
        if result_code == 0:
            callback_metadata = stk_callback.get("CallbackMetadata", {})
            items = callback_metadata.get("Item", [])
            
            receipt = None
            for item in items:
                if item.get("Name") == "MpesaReceiptNumber":
                    receipt = str(item.get("Value", ""))
                    break
            
            # Mark invoice as paid
            supabase.table("invoices").update({
                "payment_status": "paid",
                "payment_method": "mpesa",
                "mpesa_receipt": receipt,
            }).eq("mpesa_checkout_request_id", checkout_request_id).execute()
        
        return jsonify({"ok": True}), 200
    
    except Exception as e:
        print(f"Callback error: {str(e)}")
        return jsonify({"ok": True}), 200


@app.route("/password-reset", methods=["OPTIONS", "POST"])
def password_reset():
    """
    Send password reset email.
    Rate limited: 3 per email per hour.
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
        
        # Rate limit check
        rate_limit_result = supabase.rpc("check_rate_limit", {
            "p_key": f"password_reset:{email.lower()}",
            "p_max_count": 3,
            "p_window_seconds": 3600,
        }).execute()
        
        if not rate_limit_result.data:
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
