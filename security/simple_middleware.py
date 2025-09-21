"""
Simplified Security Middleware for HL7 MedDataFlow platform
Basic security headers and audit logging
"""
import time
import logging
from typing import Dict, Any
import uuid

logger = logging.getLogger(__name__)

def add_security_headers(response):
    """Add security headers to response"""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # CSP for healthcare data protection
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
    response.headers["Content-Security-Policy"] = csp
    return response

def log_request(request, user_id=None):
    """Simple request logging"""
    request_id = str(uuid.uuid4())

    # Check if this is a sensitive endpoint
    sensitive_endpoints = ["/api/hl7/", "/api/vendor/", "/api/workflows/", "/api/admin/"]
    is_sensitive = any(request.url.path.startswith(endpoint) for endpoint in sensitive_endpoints)

    if is_sensitive:
        client_ip = getattr(request.client, 'host', 'unknown') if request.client else 'unknown'

        logger.info(
            f"AUDIT_REQUEST: {request_id} | "
            f"Method: {request.method} | "
            f"Path: {request.url.path} | "
            f"IP: {client_ip} | "
            f"User: {user_id}"
        )

    return request_id