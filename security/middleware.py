"""
Security middleware for HL7 MedDataFlow platform
Implements security headers, rate limiting, and audit logging
"""
import time
import logging
from typing import Dict, Any
from collections import defaultdict, deque
import uuid

try:
    from fastapi import Request, Response, HTTPException
    from fastapi.middleware.base import BaseHTTPMiddleware
except ImportError:
    # Fallback for older FastAPI versions
    from fastapi import Request, HTTPException
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

logger = logging.getLogger(__name__)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        # HSTS for HTTPS
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

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

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware with sliding window"""

    def __init__(self, app, requests_per_minute: int = 60, requests_per_hour: int = 1000):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.minute_window = defaultdict(deque)
        self.hour_window = defaultdict(deque)

    def _clean_old_requests(self, window: Dict, max_age: int):
        """Remove old entries from sliding window"""
        current_time = time.time()
        for ip in list(window.keys()):
            timestamps = window[ip]
            while timestamps and current_time - timestamps[0] > max_age:
                timestamps.popleft()
            if not timestamps:
                del window[ip]

    def _check_rate_limit(self, ip: str) -> bool:
        """Check if IP is within rate limits"""
        current_time = time.time()

        # Clean old entries
        self._clean_old_requests(self.minute_window, 60)
        self._clean_old_requests(self.hour_window, 3600)

        # Check minute limit
        minute_requests = len(self.minute_window[ip])
        if minute_requests >= self.requests_per_minute:
            return False

        # Check hour limit
        hour_requests = len(self.hour_window[ip])
        if hour_requests >= self.requests_per_hour:
            return False

        # Add current request
        self.minute_window[ip].append(current_time)
        self.hour_window[ip].append(current_time)

        return True

    async def dispatch(self, request: Request, call_next):
        # Get client IP
        client_ip = request.client.host
        if "x-forwarded-for" in request.headers:
            client_ip = request.headers["x-forwarded-for"].split(",")[0].strip()

        # Skip rate limiting for health checks
        if request.url.path in ["/api/health", "/health", "/healthz"]:
            return await call_next(request)

        # Check rate limit
        if not self._check_rate_limit(client_ip):
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please try again later.",
                headers={"Retry-After": "60"}
            )

        return await call_next(request)

class AuditLogMiddleware(BaseHTTPMiddleware):
    """Audit logging for HIPAA compliance"""

    def __init__(self, app):
        super().__init__(app)
        self.sensitive_endpoints = {
            "/api/hl7/", "/api/vendor/", "/api/workflows/",
            "/api/admin/", "/api/tenant-admin/"
        }

    async def dispatch(self, request: Request, call_next):
        # Generate request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        start_time = time.time()

        # Check if this is a sensitive endpoint
        is_sensitive = any(request.url.path.startswith(endpoint)
                          for endpoint in self.sensitive_endpoints)

        # Log request
        if is_sensitive:
            client_ip = request.client.host
            if "x-forwarded-for" in request.headers:
                client_ip = request.headers["x-forwarded-for"].split(",")[0].strip()

            user_id = None
            if hasattr(request.state, 'user') and request.state.user:
                user_id = request.state.user.get('id')

        # Process request
        try:
            response = await call_next(request)

            # Log response for sensitive endpoints
            if is_sensitive:
                duration = time.time() - start_time

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            # Log errors for sensitive endpoints
            if is_sensitive:
                duration = time.time() - start_time
                logger.error(
                    f"AUDIT_ERROR: {request_id} | "
                    f"Error: {str(e)} | "
                    f"Duration: {duration:.3f}s"
                )
            raise

class InputValidationMiddleware(BaseHTTPMiddleware):
    """Input validation middleware for additional security"""

    def __init__(self, app, max_request_size: int = 50 * 1024 * 1024):  # 50MB default
        super().__init__(app)
        self.max_request_size = max_request_size

    async def dispatch(self, request: Request, call_next):
        # Check content length
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_request_size:
            logger.warning(f"Request too large: {content_length} bytes from {request.client.host}")
            raise HTTPException(
                status_code=413,
                detail=f"Request too large. Maximum size is {self.max_request_size} bytes."
            )

        # Check for suspicious headers
        suspicious_headers = [
            "x-forwarded-host", "x-original-host", "x-rewrite-url"
        ]

        for header in suspicious_headers:
            if header in request.headers:
                value = request.headers[header]
                if len(value) > 200 or any(char in value for char in ['<', '>', '"', "'"]):
                    logger.warning(f"Suspicious header {header}: {value} from {request.client.host}")
                    raise HTTPException(status_code=400, detail="Invalid request headers")

        return await call_next(request)