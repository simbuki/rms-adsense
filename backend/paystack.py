"""
Paystack card payments for RMS AdSense.

Endpoints (registered on the main app by register_paystack):
1. POST /paystack-initialize — start a card transaction for an invoice
2. POST /paystack-verify     — confirm a transaction after the popup closes
3. POST /paystack-webhook    — Paystack's server-to-server charge notifications

Environment variables:
  PAYSTACK_SECRET_KEY  sk_test_... or sk_live_... Set it on the host (Render)
                       only; never commit it or put it in the front end.
  PAYSTACK_BASE_URL    Optional, defaults to https://api.paystack.co

The public key (pk_...) is not used here; it lives in public/assets/config.js.
"""

import hashlib
import hmac
import os
import secrets

import requests
from flask import Blueprint, jsonify, request

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_BASE_URL = os.getenv("PAYSTACK_BASE_URL", "https://api.paystack.co").rstrip("/")
PAYSTACK_CURRENCY = "KES"


def _paystack_headers():
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def verify_signature(raw_body, signature):
    """Paystack signs webhooks with HMAC-SHA512 of the raw body, keyed by the secret key."""
    if not PAYSTACK_SECRET_KEY or not signature:
        return False
    expected = hmac.new(PAYSTACK_SECRET_KEY.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def fetch_transaction(reference):
    """Ask Paystack for a transaction's real state. Returns the `data` dict, or None."""
    response = requests.get(
        f"{PAYSTACK_BASE_URL}/transaction/verify/{requests.utils.quote(reference, safe='')}",
        headers=_paystack_headers(),
        timeout=15,
    )
    if response.status_code != 200:
        print(f"Paystack verify failed for {reference}: {response.status_code} {response.text}")
        return None
    body = response.json()
    return body.get("data") if body.get("status") else None


def register_paystack(app, supabase, require_auth):
    bp = Blueprint("paystack", __name__)

    if not PAYSTACK_SECRET_KEY:
        print("WARNING: PAYSTACK_SECRET_KEY is not set; card payments are disabled")

    def load_invoice(**match):
        column, value = next(iter(match.items()))
        result = supabase.table("invoices").select(
            "id, amount, payment_status, paystack_reference, bookings(client_id)"
        ).eq(column, value).execute()
        return result.data[0] if result.data else None

    def settle_if_paid(invoice, txn):
        """
        Mark the invoice paid if Paystack's own record of the transaction shows
        the full amount was charged in KES. Returns True when the invoice is paid.
        """
        if invoice.get("payment_status") == "paid":
            return True
        if not txn or txn.get("status") != "success":
            return False
        if txn.get("reference") != invoice.get("paystack_reference"):
            return False
        if txn.get("currency") != PAYSTACK_CURRENCY:
            print(f"Paystack currency mismatch on invoice {invoice['id']}: {txn.get('currency')}")
            return False
        if int(txn.get("amount") or 0) != int(invoice.get("amount") or 0) * 100:
            print(f"Paystack amount mismatch on invoice {invoice['id']}: {txn.get('amount')}")
            return False

        supabase.table("invoices").update({
            "payment_status": "paid",
            "payment_method": "card",
        }).eq("id", invoice["id"]).eq("payment_status", "unpaid").execute()
        return True

    def owned_by_caller(invoice):
        booking = invoice.get("bookings") or {}
        return isinstance(booking, dict) and booking.get("client_id") == request.user.id

    @bp.route("/paystack-initialize", methods=["POST"])
    @require_auth
    def paystack_initialize():
        """
        Start a Paystack transaction for an invoice.

        POST body: { "invoiceId": 123 }
        Returns: { "accessCode": "...", "reference": "..." } for the Paystack popup.
        """
        if not PAYSTACK_SECRET_KEY:
            return jsonify({"error": "Card payments are not enabled yet"}), 503
        try:
            invoice_id = (request.get_json() or {}).get("invoiceId")
            if not invoice_id:
                return jsonify({"error": "invoiceId required"}), 400

            invoice = load_invoice(id=invoice_id)
            if not invoice:
                return jsonify({"error": "Invoice not found"}), 404
            if not owned_by_caller(invoice):
                return jsonify({"error": "This invoice does not belong to you"}), 403
            if invoice.get("payment_status") == "paid":
                return jsonify({"error": "Invoice already paid"}), 400

            email = getattr(request.user, "email", None)
            if not email:
                return jsonify({"error": "Your account has no email address for the card receipt"}), 400

            # A fresh reference per attempt, so a retried or abandoned popup
            # never collides with an earlier transaction.
            reference = f"INV-{invoice['id']}-{secrets.token_hex(6)}"
            payload = {
                "email": email,
                "amount": int(invoice["amount"]) * 100,  # Paystack wants the lowest unit (cents)
                "currency": PAYSTACK_CURRENCY,
                "reference": reference,
                "channels": ["card"],
                "metadata": {"invoice_id": invoice["id"]},
            }
            response = requests.post(
                f"{PAYSTACK_BASE_URL}/transaction/initialize",
                json=payload,
                headers=_paystack_headers(),
                timeout=20,
            )
            body = response.json() if response.content else {}
            if response.status_code != 200 or not body.get("status"):
                return jsonify({"error": body.get("message", "Could not start the card payment")}), 502

            supabase.table("invoices").update({
                "paystack_reference": reference,
            }).eq("id", invoice["id"]).execute()

            return jsonify({
                "ok": True,
                "accessCode": body["data"]["access_code"],
                "reference": reference,
            }), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/paystack-verify", methods=["POST"])
    @require_auth
    def paystack_verify():
        """
        Confirm a card payment once the popup reports success.

        POST body: { "reference": "INV-12-..." }
        """
        if not PAYSTACK_SECRET_KEY:
            return jsonify({"error": "Card payments are not enabled yet"}), 503
        try:
            reference = (request.get_json() or {}).get("reference")
            if not reference:
                return jsonify({"error": "reference required"}), 400

            invoice = load_invoice(paystack_reference=reference)
            if not invoice:
                return jsonify({"error": "Payment not found"}), 404
            if not owned_by_caller(invoice):
                return jsonify({"error": "This invoice does not belong to you"}), 403

            paid = settle_if_paid(invoice, fetch_transaction(reference))
            return jsonify({"ok": True, "paid": paid}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/paystack-webhook", methods=["POST"])
    def paystack_webhook():
        """
        Paystack's charge notifications. Set this URL in the Paystack dashboard
        under Settings > API Keys & Webhooks. Catches payments where the
        customer closed the page before /paystack-verify ran.
        """
        raw_body = request.get_data()
        if not verify_signature(raw_body, request.headers.get("x-paystack-signature", "")):
            return jsonify({"error": "Invalid signature"}), 401

        try:
            event = request.get_json(force=True, silent=True) or {}
            if event.get("event") == "charge.success":
                reference = (event.get("data") or {}).get("reference")
                invoice = load_invoice(paystack_reference=reference) if reference else None
                if invoice:
                    # Re-check with Paystack rather than trusting the event body alone.
                    settle_if_paid(invoice, fetch_transaction(reference))
        except Exception as e:
            print(f"Paystack webhook error: {str(e)}")

        # Always 200 once the signature checks out, or Paystack keeps retrying.
        return jsonify({"ok": True}), 200

    app.register_blueprint(bp)
