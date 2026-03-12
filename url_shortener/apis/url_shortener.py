import frappe
import string
import random
import requests
from frappe.utils import now_datetime, add_days, nowdate


def generate_token(length=6):
    """Generate a unique random alphanumeric token."""
    characters = string.ascii_letters + string.digits
    while True:
        token = ''.join(random.choices(characters, k=length))
        if not frappe.db.exists("URL Shortener", {"token": token}):
            return token


@frappe.whitelist(allow_guest=True)
def create_short_url(original_url, description=None, expiry_days=None, allowed_ips=None):
    """
    Register a new short URL mapping.

    Args:
        original_url : Real internal ERPNext endpoint to protect
                       e.g. "api/method/apex_reports.apis.website_lead.create_lead_from_website"
        description  : Human-readable label e.g. "Website Lead Form"
        expiry_days  : Days until expiry (optional)
        allowed_ips  : Comma-separated IP whitelist (optional)

    Returns:
        dict with token and shareable short URL

    Usage from console:
        frappe.call("apex_reports.apis.url_shortener.create_short_url", {
            original_url: "api/method/apex_reports.apis.website.create_lead_from_website",
            description: "Website Lead Form",
            expiry_days: 365
        })
    """
    token = generate_token()
    expiry_date = add_days(nowdate(), int(expiry_days)) if expiry_days else None

    doc = frappe.get_doc({
        "doctype": "URL Shortener",
        "token": token,
        "original_url": original_url,
        "description": description or "",
        "is_active": 1,
        "expiry_date": expiry_date,
        "allowed_ips": allowed_ips or "",
        "hit_count": 0,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    site_name = frappe.local.site

    # Clean short URL — only site name and token visible
    # Nothing about Frappe, app name, or endpoint exposed
    short_url = f"https://{site_name}/r/{token}"

    return {
        "success": True,
        "token": token,
        "short_url": short_url,
        "original_url": original_url,
        "expiry_date": str(expiry_date) if expiry_date else "Never"
    }


@frappe.whitelist()
def deactivate_token(token):
    """Manually deactivate a short URL token."""
    if not frappe.db.exists("URL Shortener", {"token": token}):
        return {"success": False, "message": "Token not found."}

    frappe.db.set_value("URL Shortener", {"token": token}, "is_active", 0)
    frappe.db.commit()

    return {"success": True, "message": f"Token {token} has been deactivated."}


@frappe.whitelist()
def rotate_token(token):
    """
    Rotate a token — deactivate the old one and issue a new short URL
    for the same original endpoint. Use this if a token is compromised.
    """
    if not frappe.db.exists("URL Shortener", {"token": token}):
        return {"success": False, "message": "Token not found."}

    old_doc = frappe.get_doc("URL Shortener", {"token": token})

    # Deactivate old token
    old_doc.db_set("is_active", 0)

    # Issue new token for same endpoint
    result = create_short_url(
        original_url=old_doc.original_url,
        description=old_doc.description,
        allowed_ips=old_doc.allowed_ips
    )

    frappe.log_error(
        title="URL Shortener Token Rotated",
        message=f"Old token: {token} deactivated. New token: {result['token']} issued."
    )

    return {
        "success": True,
        "old_token": token,
        "new_token": result["token"],
        "new_short_url": result["short_url"]
    }


@frappe.whitelist()
def get_token_stats(token):
    """Return usage stats and recent logs for a given token."""
    if not frappe.db.exists("URL Shortener", {"token": token}):
        return {"success": False, "message": "Token not found."}

    doc = frappe.get_doc("URL Shortener", {"token": token})
    site_name = frappe.local.site

    logs = frappe.get_all(
        "URL Shortener Log",
        filters={"token": token},
        fields=["status", "caller_ip", "timestamp", "response_code", "error_message"],
        order_by="timestamp desc",
        limit=50
    )

    return {
        "success": True,
        "token": token,
        "short_url": f"https://{site_name}/r/{token}",
        "description": doc.description,
        "is_active": doc.is_active,
        "hit_count": doc.hit_count,
        "last_accessed": str(doc.last_accessed) if doc.last_accessed else "Never",
        "expiry_date": str(doc.expiry_date) if doc.expiry_date else "Never",
        "allowed_ips": doc.allowed_ips or "All IPs allowed",
        "recent_logs": logs
    }