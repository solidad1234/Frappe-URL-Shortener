"""
url_shortener/www/r.py

URL Shortener endpoint registered via hooks.py:
    website_route_rules: {"from_route": "/r/<token>", "to_route": "r"}
    before_request:      "url_shortener.www.r.handle_shortener_request"

Architecture
------------
All Frappe www pages run through serve.py -> TemplatePage.render(), which
ALWAYS renders the Jinja template and overwrites any response we set on
frappe.local.response.  There is no Frappe-native way to return JSON from
a www page because every exception raised inside get_context() is caught by
serve.py's "except Exception" and converted to an HTML error page.

Solution: use a before_request hook (hooks.py -> before_request).
Before-request hooks are called inside app.py's outer try block, BEFORE
get_response() is invoked:

    try:
        init_request(request)       <-- before_request hooks run here
        validate_auth()
        ...
        response = get_response()   <-- www pipeline (never reached for /r/)
        ...
    except HTTPException as e:
        return e                    <-- our JSON response is returned here
    except Exception as e:
        ...

handle_shortener_request() checks if the path is a /r/<token> route.  If so,
it processes the request and raises an HTTPException whose .response is set
to a pre-built WerkzeugResponse(JSON).  app.py catches HTTPException and
returns its .response directly -- clean JSON, correct Content-Type, correct
status code.

get_context() is kept as a no-op so the www route registration still resolves
(PathResolver needs the .html + .py pair to exist), but it is never reached
because the before_request hook short-circuits the pipeline first.
"""
import frappe
import importlib
import json
from frappe.utils import now_datetime


# ── Public constants for Frappe's www route registration ──────────────────────
no_cache = 1
allow_guest = True


# ── before_request hook ───────────────────────────────────────────────────────
def handle_shortener_request():
    """
    Called by Frappe for EVERY request via the before_request hook.
    Only acts on /r/<token> paths; ignores everything else.
    Raises werkzeug.exceptions.HTTPException so app.py returns our JSON
    response directly, bypassing the www rendering pipeline.
    """
    path = frappe.local.request.path  # e.g.  /r/PS8maX

    if not path.startswith("/r/"):
        return  # not our route, let Frappe handle normally

    token = path[3:].strip("/")  # extract token from /r/<token>
    if not token:
        _raise_json({"success": False, "message": "An error occurred."}, 400)

    _handle_request(token)


# ── www stub (must exist so PathResolver can resolve the route) ───────────────
def get_context(context):
    """
    No-op stub.  In practice this is NEVER reached because the
    before_request hook raises HTTPException before get_response() runs.
    Kept so that Frappe's PathResolver finds the r.html + r.py pair and
    doesn't return a 404 for /r/* paths on the first boot.
    """
    pass


# ── Core helpers ──────────────────────────────────────────────────────────────

def _make_json_response(data, status_code=200):
    """Build a WerkzeugResponse with application/json content."""
    from werkzeug.wrappers import Response as WerkzeugResponse

    return WerkzeugResponse(
        response=json.dumps(data),
        status=status_code,
        mimetype="application/json",
    )


def _raise_json(data, status_code=200):
    """
    Raise a werkzeug HTTPException whose .response is our JSON payload.
    app.py catches HTTPException with:
        except HTTPException as e: return e
    Werkzeug Response objects are valid WSGI apps, so 'return e' sends the
    JSON body + Content-Type: application/json to the client.
    """
    from werkzeug.exceptions import HTTPException

    class _JsonResponse(HTTPException):
        pass

    exc = _JsonResponse()
    exc.response = _make_json_response(data, status_code)
    raise exc


def _get_caller_ip():
    ip = (
        frappe.local.request.headers.get("X-Real-Client-IP")
        or frappe.local.request.headers.get("X-Forwarded-For")
        or frappe.local.request.headers.get("X-Real-IP")
        or (frappe.local.request_ip if hasattr(frappe.local, "request_ip") else None)
    )
    if ip:
        ip = ip.split(",")[0].strip()
    return ip


def _create_log(token, caller_ip, status, response_code, error_message=None):
    try:
        frappe.get_doc({
            "doctype": "URL Shortener Log",
            "token": token,
            "caller_ip": caller_ip,
            "timestamp": now_datetime(),
            "status": status,
            "response_code": response_code,
            "error_message": error_message,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(
            title="URL Shortener Log Error",
            message=f"Failed to create log: {str(e)}\n{frappe.get_traceback()}",
        )


def _resolve_method(method_path):
    """Convert dotted method path to callable Python function via importlib."""
    parts = method_path.strip().split(".")
    if len(parts) < 2:
        frappe.throw(f"Invalid method path: {method_path}")

    module_path = ".".join(parts[:-1])
    function_name = parts[-1]

    module = importlib.import_module(module_path)
    func = getattr(module, function_name, None)

    if not func:
        frappe.throw(f"Function '{function_name}' not found in module '{module_path}'")

    return func


def _authenticate_request():
    """
    Validate the Authorization header and set the session user.
    Authorization header is MANDATORY - no fallback, no exceptions.
    """
    auth_header = frappe.request.headers.get("Authorization", "")

    if not auth_header or not auth_header.startswith("token "):
        frappe.throw(
            "Authorization header is required.",
            exc=frappe.AuthenticationError,
        )

    try:
        api_key, api_secret = auth_header.replace("token ", "").strip().split(":")
    except ValueError:
        frappe.throw(
            "Invalid Authorization header format. Expected: token api_key:api_secret",
            exc=frappe.AuthenticationError,
        )

    user = frappe.db.get_value("User", {"api_key": api_key}, "name")
    if not user:
        frappe.throw("Invalid API credentials.", exc=frappe.AuthenticationError)

    from frappe.utils.password import get_decrypted_password
    stored_secret = get_decrypted_password("User", user, "api_secret")
    if api_secret != stored_secret:
        frappe.throw("Invalid API credentials.", exc=frappe.AuthenticationError)

    frappe.set_user(user)
    return user


def _handle_request(token):
    caller_ip = _get_caller_ip()
    log_data = {"token": token, "caller_ip": caller_ip}

    try:
        # Token lookup
        if not frappe.db.exists("URL Shortener", {"token": token}):
            _create_log(status="Blocked", response_code=404,
                        error_message="Token not found", **log_data)
            _raise_json({"success": False, "message": "An error occurred."}, 404)

        doc = frappe.get_doc("URL Shortener", {"token": token})

        # Active check
        if not doc.is_active:
            _create_log(status="Blocked", response_code=403,
                        error_message="Token is inactive", **log_data)
            _raise_json({"success": False, "message": "An error occurred."}, 403)

        # Expiry check
        if doc.expiry_date and doc.expiry_date < frappe.utils.today():
            doc.db_set("is_active", 0)
            _create_log(status="Expired", response_code=403,
                        error_message=f"Token expired on {doc.expiry_date}", **log_data)
            _raise_json({"success": False, "message": "An error occurred."}, 403)

        # IP whitelist check
        if doc.allowed_ips:
            allowed = [ip.strip() for ip in doc.allowed_ips.split(",")]
            if caller_ip not in allowed:
                _create_log(status="Blocked", response_code=403,
                            error_message=f"IP {caller_ip} not in allowed list", **log_data)
                _raise_json({"success": False, "message": "An error occurred."}, 403)

        # Authenticate - Authorization header is mandatory
        try:
            _authenticate_request()
        except frappe.AuthenticationError as e:
            _create_log(status="Blocked", response_code=401,
                        error_message=f"Authentication failed: {str(e)}", **log_data)
            _raise_json({"success": False, "message": "An error occurred."}, 401)

        # Update stats
        doc.db_set("hit_count", (doc.hit_count or 0) + 1)
        doc.db_set("last_accessed", now_datetime())

        # Parse method path
        original_url = doc.original_url.strip().lstrip("/")
        if "api/method/" in original_url:
            method_path = original_url.split("api/method/")[-1].split("?")[0].strip()
        else:
            method_path = original_url.split("?")[0].strip()

        # Collect params - handle both form-data and raw JSON body
        params = {k: v for k, v in frappe.form_dict.items() if k != "token"}

        if not params:
            try:
                json_body = frappe.request.get_json(silent=True, force=True)
                if json_body and isinstance(json_body, dict):
                    params = {k: v for k, v in json_body.items() if k != "token"}
            except Exception:
                pass

        # Call the target function via importlib
        func = _resolve_method(method_path)
        result = func(**params)

        _create_log(status="Success", response_code=200, **log_data)

        # Return JSON response - _raise_json propagates to app.py's HTTPException handler
        _raise_json(result if isinstance(result, dict) else {"data": result}, 200)

    except Exception as e:
        # Re-raise our own _JsonResponse exceptions so they reach app.py unchanged
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException) and getattr(e, "response", None) is not None:
            raise

        frappe.log_error(
            title="URL Shortener Resolve Error",
            message=f"Token: {token}\nError: {str(e)}\n{frappe.get_traceback()}",
        )
        _create_log(status="Failed", response_code=500,
                    error_message=str(e), **log_data)
        _raise_json({"success": False, "message": "An error occurred."}, 500)