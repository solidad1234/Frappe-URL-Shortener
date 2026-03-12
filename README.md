# URL Shortener

A custom Frappe app designed to obfuscate project structure and provide secure, trackable, and manageable URL redirection for ERPNext.

## Features

- **Security**: Hide internal ERPNext method paths (e.g., `api/method/...`).
- **IP Whitelisting**: Restrict access to specific shortened URLs by caller IP.
- **Expiry**: Set automatic expiration dates for shortened links.
- **Logging**: Track every hit with caller IP, timestamp, and response status.
- **Token Rotation**: Easily deactivate and rotate compromised tokens.
- **Seamless Integration**: Works via a `before_request` hook to ensure clean JSON responses for API-style redirects.

## Installation

Install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/[your-repo]/url_shortener --branch main
bench install-app url_shortener
```

## Usage Guide

### 1. Create a Shortened URL
You can create a new mapping by calling the `create_short_url` endpoint.

**Endpoint:** `POST /api/method/url_shortener.apis.url_shortener.create_short_url`

**Payload (JSON):**
```json
{
    "original_url": "api/method/your_app.your_module.your_function",
    "description": "Marketing Campaign Lead Form",
    "expiry_days": 30,
    "allowed_ips": "192.168.1.1, 203.0.113.42"
}
```

**Response:**
```json
{
    "success": true,
    "token": "aB3cD5",
    "short_url": "https://your-site.com/r/aB3cD5",
    "original_url": "api/method/your_app.your_module.your_function",
    "expiry_date": "2024-04-12"
}
```

### 2. Using the Shortened URL
Share the `short_url` with third parties. When they call `https://<sitename>/r/<token>`, the app:
1. Validates the token and its status (Active/Expired).
2. Checks the caller IP against the whitelist (if configured).
3. **Requires Mandatory Authentication**: The caller must provide a valid Frappe `Authorization` header (`token api_key:api_secret`).
4. Proxies the request parameters to the `original_url`.
5. Logs the request in the **URL Shortener Log**.
6. Returns the JSON response from the target function.

### 3. Monitoring and Management
- **URL Shortener Doctype**: View and manage all active tokens, see hit counts, and last accessed timestamps.
- **URL Shortener Log Doctype**: Audit trail for all requests including blocked attempts and errors.

## Technical Overview

### Redirection Logic
The redirection is handled via a `before_request` hook in `hooks.py`. This allows the app to intercept `/r/<token>` routes and return pure JSON responses, bypassing the standard Frappe `www` HTML rendering pipeline.

### Doctypes
- **URL Shortener**: Stores the mapping between tokens and internal methods.
- **URL Shortener Log**: Stores execution history for each token.

## Contributing

This app uses `pre-commit` for code formatting and linting.

```bash
cd apps/url_shortener
pre-commit install
```

## License

MIT
