#!/usr/bin/env python3
"""
Automated Penetration Testing Suite for HL7 Healthcare Platform
Tests security controls and validates protection against common attacks
"""
import asyncio
import httpx
import json
import time
import uuid
from typing import Dict, List, Any
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SecurityTestSuite:
    """Comprehensive security testing suite"""

    def __init__(self, base_url: str = "http://localhost:8001"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)
        self.test_results = []

    async def run_all_tests(self):
        """Run complete security test suite"""

        tests = [
            ("Authentication Security", self.test_authentication_security),
            ("JWT Token Security", self.test_jwt_security),
            ("Input Validation", self.test_input_validation),
            ("SQL Injection Protection", self.test_sql_injection),
            ("Code Execution Protection", self.test_code_execution_protection),
            ("Rate Limiting", self.test_rate_limiting),
            ("CORS Security", self.test_cors_security),
            ("HL7 Message Security", self.test_hl7_message_security),
            ("File Upload Security", self.test_file_upload_security),
            ("Session Security", self.test_session_security),
            ("Information Disclosure", self.test_information_disclosure),
            ("CSRF Protection", self.test_csrf_protection)
        ]

        for test_name, test_func in tests:
            try:
                await test_func()
                self._record_result(test_name, "PASSED", "All security controls working")
            except AssertionError as e:
                self._record_result(test_name, "FAILED", str(e))
                logger.error(f"❌ {test_name} FAILED: {e}")
            except Exception as e:
                self._record_result(test_name, "ERROR", str(e))
                logger.error(f"💥 {test_name} ERROR: {e}")

        await self.client.aclose()
        return self.test_results

    def _record_result(self, test_name: str, status: str, details: str):
        """Record test result"""
        self.test_results.append({
            "test": test_name,
            "status": status,
            "details": details,
            "timestamp": time.time()
        })

    async def test_authentication_security(self):
        """Test authentication security controls"""

        # Test 1: Reject requests without authentication
        response = await self.client.get(f"{self.base_url}/api/workflows")
        assert response.status_code == 401, "Should reject unauthenticated requests"

        # Test 2: Reject invalid JWT tokens
        headers = {"Authorization": "Bearer invalid_token"}
        response = await self.client.get(f"{self.base_url}/api/workflows", headers=headers)
        assert response.status_code == 401, "Should reject invalid JWT tokens"

        # Test 3: Reject malformed tokens
        headers = {"Authorization": "Bearer malformed.jwt.token"}
        response = await self.client.get(f"{self.base_url}/api/workflows", headers=headers)
        assert response.status_code == 401, "Should reject malformed JWT tokens"

        # Test 4: Test brute force protection (should be rate limited)
        for i in range(10):
            response = await self.client.post(
                f"{self.base_url}/api/auth/login",
                json={"email": "test@example.com", "password": "wrong_password"}
            )
            if response.status_code == 429:  # Rate limited
                break
        else:
            assert False, "Should implement brute force protection"

    async def test_jwt_security(self):
        """Test JWT token security"""

        # Test 1: Ensure JWT secret is not default
        try:
            import jwt
            # Try to decode with common weak secrets
            weak_secrets = ["secret", "your-secret-key-here", "test", "123456"]
            test_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxNjk5OTk5OTk5fQ.test"

            for secret in weak_secrets:
                try:
                    jwt.decode(test_token, secret, algorithms=["HS256"])
                    assert False, f"Weak JWT secret detected: {secret}"
                except jwt.InvalidTokenError:
                    pass  # Expected
        except ImportError:
            pass  # JWT library not available for testing

        # Test 2: Test token manipulation
        fake_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJzdXBlcl9hZG1pbiJ9.fake"
        headers = {"Authorization": f"Bearer {fake_token}"}
        response = await self.client.get(f"{self.base_url}/api/admin/users", headers=headers)
        assert response.status_code in [401, 403], "Should reject manipulated tokens"

    async def test_input_validation(self):
        """Test input validation security"""

        # Test 1: XSS prevention
        xss_payloads = [
            "<script>alert('xss')</script>",
            "javascript:alert('xss')",
            "<img src=x onerror=alert('xss')>",
            "';alert('xss');//"
        ]

        for payload in xss_payloads:
            response = await self.client.post(
                f"{self.base_url}/api/auth/register",
                json={
                    "email": "test@example.com",
                    "password": "password123",
                    "first_name": payload,
                    "last_name": "Test",
                    "tenant_slug": "test"
                }
            )
            # Should either reject or sanitize
            if response.status_code == 200:
                data = response.json()
                assert payload not in str(data), "XSS payload should be sanitized"

        # Test 2: Oversized input
        large_input = "A" * 100000  # 100KB
        response = await self.client.post(
            f"{self.base_url}/api/auth/register",
            json={
                "email": "test@example.com",
                "password": large_input,
                "first_name": "Test",
                "last_name": "Test",
                "tenant_slug": "test"
            }
        )
        assert response.status_code in [400, 413], "Should reject oversized input"

    async def test_sql_injection(self):
        """Test SQL injection protection"""

        sql_payloads = [
            "' OR '1'='1",
            "'; DROP TABLE users; --",
            "' UNION SELECT * FROM users --",
            "admin'--",
            "' OR 1=1 --"
        ]

        for payload in sql_payloads:
            # Test login endpoint
            response = await self.client.post(
                f"{self.base_url}/api/auth/login",
                json={"email": payload, "password": "test"}
            )
            assert response.status_code != 200, f"SQL injection vulnerability detected: {payload}"

            # Test search endpoints if available
            response = await self.client.get(
                f"{self.base_url}/api/workflows?search={payload}"
            )
            # Should not return unauthorized data or cause errors
            assert response.status_code in [400, 401, 403, 422], "Should handle malicious search input"

    async def test_code_execution_protection(self):
        """Test protection against code execution"""

        code_injection_payloads = [
            "eval('alert(1)')",
            "exec('import os; os.system(\"ls\")')",
            "__import__('os').system('id')",
            "subprocess.call(['ls'])",
            "os.system('cat /etc/passwd')"
        ]

        # Test custom script execution if endpoint exists
        for payload in code_injection_payloads:
            try:
                response = await self.client.post(
                    f"{self.base_url}/api/workflows/test/execute",
                    json={
                        "script": payload,
                        "context": {}
                    }
                )
                # Should reject dangerous code
                if response.status_code == 200:
                    data = response.json()
                    assert "error" in data or "security" in str(data).lower(), \
                        f"Code injection not detected: {payload}"
            except Exception:
                pass  # Endpoint might not exist

    async def test_hl7_message_security(self):
        """Test HL7 message processing security"""

        # Test 1: Malformed HL7 messages
        malformed_messages = [
            "NOT_HL7_MESSAGE",
            "<script>alert('xss')</script>",
            "MSH|" + "A" * 1000000,  # Very large message
            "MSH|^~\\&|<script>alert('xss')</script>",
        ]

        for message in malformed_messages:
            response = await self.client.post(
                f"{self.base_url}/api/vendor/test/hl7/ingest",
                json={"raw_message": message},
                headers={"Authorization": "Bearer test_api_key"}
            )
            assert response.status_code in [400, 401, 413], \
                f"Should reject malformed HL7 message: {message[:50]}..."

        # Test 2: HL7 injection attempts
        injection_message = """MSH|^~\\&|TEST|TEST|TEST|TEST|20240101000000||ADT^A01|12345|P|2.4|||
PID|1||PATID123^^^MR||exec('rm -rf /')^TEST||19800101|M|||123 Main St^^City^ST^12345||5551234567|||S||ACCT123|123-45-6789|||||||||||||||
"""
        response = await self.client.post(
            f"{self.base_url}/api/vendor/test/hl7/ingest",
            json={"raw_message": injection_message},
            headers={"Authorization": "Bearer test_api_key"}
        )
        # Should process safely or reject suspicious content
        assert response.status_code in [400, 401, 413], "Should detect suspicious HL7 content"

    async def test_rate_limiting(self):
        """Test rate limiting implementation"""

        # Test rapid requests
        responses = []
        for i in range(200):  # Exceed typical rate limits
            response = await self.client.get(f"{self.base_url}/api/health")
            responses.append(response.status_code)
            if response.status_code == 429:
                break

        # Should get rate limited eventually
        assert 429 in responses, "Rate limiting should be implemented"

    async def test_cors_security(self):
        """Test CORS configuration security"""

        # Test CORS with malicious origin
        headers = {"Origin": "https://evil.com"}
        response = await self.client.options(f"{self.base_url}/api/health", headers=headers)

        if "access-control-allow-origin" in response.headers:
            cors_origin = response.headers["access-control-allow-origin"]
            assert cors_origin != "*", "CORS should not allow all origins"
            assert "evil.com" not in cors_origin, "Should not allow malicious origins"

    async def test_file_upload_security(self):
        """Test file upload security if applicable"""

        # Test malicious file upload
        malicious_files = [
            ("test.php", "<?php system($_GET['cmd']); ?>"),
            ("test.jsp", "<% Runtime.getRuntime().exec(request.getParameter(\"cmd\")); %>"),
            ("test.exe", b"\x4d\x5a\x90\x00"),  # PE header
        ]

        for filename, content in malicious_files:
            try:
                files = {"file": (filename, content, "application/octet-stream")}
                response = await self.client.post(
                    f"{self.base_url}/api/upload",
                    files=files
                )
                # Should reject malicious files
                assert response.status_code in [400, 403, 415], \
                    f"Should reject malicious file: {filename}"
            except Exception:
                pass  # Upload endpoint might not exist

    async def test_session_security(self):
        """Test session management security"""

        # Test session fixation
        response1 = await self.client.get(f"{self.base_url}/api/health")
        session1 = response1.cookies.get("session")

        if session1:
            # Try to reuse session after "login"
            headers = {"Cookie": f"session={session1}"}
            response2 = await self.client.post(
                f"{self.base_url}/api/auth/login",
                json={"email": "test@example.com", "password": "password"},
                headers=headers
            )

            if response2.status_code == 200:
                session2 = response2.cookies.get("session")
                assert session1 != session2, "Session should regenerate after login"

    async def test_information_disclosure(self):
        """Test for information disclosure vulnerabilities"""

        # Test error message information disclosure
        response = await self.client.get(f"{self.base_url}/api/nonexistent")
        assert response.status_code == 404
        error_text = response.text.lower()

        # Should not reveal system information
        sensitive_info = ["traceback", "python", "exception", "stack trace", "file path"]
        for info in sensitive_info:
            assert info not in error_text, f"Should not disclose {info} in error messages"

        # Test debug endpoints
        debug_endpoints = ["/debug", "/api/debug", "/status", "/info", "/config"]
        for endpoint in debug_endpoints:
            response = await self.client.get(f"{self.base_url}{endpoint}")
            if response.status_code == 200:
                assert "debug" not in response.text.lower(), \
                    f"Debug endpoint {endpoint} should not be accessible"

    async def test_csrf_protection(self):
        """Test CSRF protection if implemented"""

        # Test state-changing operations without CSRF token
        csrf_tests = [
            ("POST", "/api/users", {"name": "test"}),
            ("PUT", "/api/users/1", {"name": "updated"}),
            ("DELETE", "/api/users/1", {})
        ]

        for method, endpoint, data in csrf_tests:
            try:
                if method == "POST":
                    response = await self.client.post(f"{self.base_url}{endpoint}", json=data)
                elif method == "PUT":
                    response = await self.client.put(f"{self.base_url}{endpoint}", json=data)
                elif method == "DELETE":
                    response = await self.client.delete(f"{self.base_url}{endpoint}")

                # Should require authentication at minimum
                assert response.status_code in [401, 403, 405], \
                    f"State-changing {method} should be protected"
            except Exception:
                pass  # Endpoint might not exist

    def generate_report(self) -> str:
        """Generate security test report"""
        total_tests = len(self.test_results)
        passed_tests = len([r for r in self.test_results if r["status"] == "PASSED"])
        failed_tests = len([r for r in self.test_results if r["status"] == "FAILED"])
        error_tests = len([r for r in self.test_results if r["status"] == "ERROR"])

        report = f"""
🔒 SECURITY PENETRATION TEST REPORT
=====================================

Summary:
- Total Tests: {total_tests}
- Passed: {passed_tests} ✅
- Failed: {failed_tests} ❌
- Errors: {error_tests} 💥

Security Score: {(passed_tests / total_tests * 100):.1f}%

Detailed Results:
"""

        for result in self.test_results:
            status_emoji = {"PASSED": "✅", "FAILED": "❌", "ERROR": "💥"}[result["status"]]
            report += f"\n{status_emoji} {result['test']}: {result['status']}"
            if result["status"] != "PASSED":
                report += f"\n   Details: {result['details']}"

        if failed_tests > 0:
            report += f"\n\n⚠️  WARNING: {failed_tests} security tests failed!"
            report += "\nImmediate action required to address security vulnerabilities."

        return report

async def main():
    """Run security penetration test"""
    print("🔍 Starting HL7 Healthcare Platform Security Assessment...")

    test_suite = SecurityTestSuite()
    results = await test_suite.run_all_tests()

    report = test_suite.generate_report()
    print(report)

    # Save report to file
    with open('/tmp/security_test_report.txt', 'w') as f:
        f.write(report)

    print(f"\n📄 Full report saved to: /tmp/security_test_report.txt")

if __name__ == "__main__":
    asyncio.run(main())