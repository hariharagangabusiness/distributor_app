"""Generates a scannable UPI payment QR code for a sale's invoice/receipt,
from the company's configured UPI ID (Company / GST Settings, next to Bank
Details). Encodes the standard "upi://pay" deep link that GPay/PhonePe/
Paytm/BHIM and other UPI apps already recognize when scanned - no payment
gateway integration or API key needed, this is just a QR of a URI.
"""
from io import BytesIO
from urllib.parse import quote
import qrcode


def build_upi_uri(upi_id, payee_name, amount=None, note=None):
    params = f"pa={quote(upi_id)}&pn={quote(payee_name or '')}&cu=INR"
    if amount and amount > 0:
        params += f"&am={amount:.2f}"
    if note:
        params += f"&tn={quote(note)}"
    return f"upi://pay?{params}"


def build_upi_qr_png(upi_id, payee_name, amount=None, note=None, box_size=6, border=2):
    """Returns PNG bytes for a QR of the UPI payment link, or None if no UPI
    ID is configured - callers should treat that the same as "don't show a
    QR code", same best-effort spirit as the rest of invoice generation."""
    if not upi_id:
        return None
    uri = build_upi_uri(upi_id, payee_name, amount, note)
    img = qrcode.make(uri, box_size=box_size, border=border)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
