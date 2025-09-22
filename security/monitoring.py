"""
Security Monitoring and Alerting System for HL7 Healthcare Platform
HIPAA-compliant audit logging and threat detection
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from enum import Enum
from collections import defaultdict, deque
import hashlib
import uuid

class SecurityEventType(Enum):
    """Types of security events for classification"""
    AUTHENTICATION_FAILURE = "auth_failure"
    AUTHENTICATION_SUCCESS = "auth_success"
    AUTHORIZATION_FAILURE = "authz_failure"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    DATA_ACCESS = "data_access"
    PHI_ACCESS = "phi_access"
    ADMIN_ACTION = "admin_action"
    SYSTEM_BREACH_ATTEMPT = "breach_attempt"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    MALICIOUS_INPUT = "malicious_input"
    WORKFLOW_EXECUTION = "workflow_execution"
    HL7_MESSAGE_PROCESSED = "hl7_processed"
    CODE_EXECUTION = "code_execution"
    SECURITY_VIOLATION = "security_violation"

class SecuritySeverity(Enum):
    """Security event severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class HIPAALogger:
    """HIPAA-compliant audit logger"""

    def __init__(self):
        self.logger = logging.getLogger("hipaa_audit")
        self.logger.setLevel(logging.INFO)

        # Create formatter for HIPAA compliance
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | AUDIT | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S UTC'
        )

        # File handler for audit logs (should be write-only, tamper-evident)
        file_handler = logging.FileHandler('/app/logs/hipaa_audit.log')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        # Console handler for development
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

    def log_event(self, event_type: SecurityEventType, severity: SecuritySeverity,
                  user_id: Optional[str] = None, tenant_id: Optional[str] = None,
                  ip_address: Optional[str] = None, user_agent: Optional[str] = None,
                  resource: Optional[str] = None, action: Optional[str] = None,
                  details: Optional[Dict[str, Any]] = None, phi_accessed: bool = False):
        """Log security event with HIPAA compliance"""

        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        audit_event = {
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": event_type.value,
            "severity": severity.value,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "resource": resource,
            "action": action,
            "phi_accessed": phi_accessed,
            "details": details or {}
        }

        # Create audit trail hash for integrity
        audit_string = json.dumps(audit_event, sort_keys=True)
        audit_hash = hashlib.sha256(audit_string.encode()).hexdigest()
        audit_event["integrity_hash"] = audit_hash

        # Log the event

        return event_id

class ThreatDetector:
    """Real-time threat detection system"""

    def __init__(self):
        self.failed_logins = defaultdict(deque)
        self.suspicious_ips = defaultdict(int)
        self.phi_access_patterns = defaultdict(deque)
        self.code_execution_attempts = defaultdict(deque)

    def detect_brute_force(self, ip_address: str, user_id: Optional[str] = None) -> bool:
        """Detect brute force authentication attempts"""
        current_time = time.time()
        window = 300  # 5 minutes

        # Track failed attempts by IP
        self.failed_logins[ip_address].append(current_time)

        # Clean old entries
        while (self.failed_logins[ip_address] and
               current_time - self.failed_logins[ip_address][0] > window):
            self.failed_logins[ip_address].popleft()

        # Check if threshold exceeded
        if len(self.failed_logins[ip_address]) >= 5:  # 5 failures in 5 minutes
            return True

        return False

    def detect_suspicious_phi_access(self, user_id: str, phi_records_accessed: int) -> bool:
        """Detect suspicious PHI access patterns"""
        current_time = time.time()
        window = 3600  # 1 hour

        self.phi_access_patterns[user_id].append((current_time, phi_records_accessed))

        # Clean old entries
        while (self.phi_access_patterns[user_id] and
               current_time - self.phi_access_patterns[user_id][0][0] > window):
            self.phi_access_patterns[user_id].popleft()

        # Calculate total records accessed in the window
        total_accessed = sum(count for _, count in self.phi_access_patterns[user_id])

        # Alert if more than 100 PHI records accessed in 1 hour
        if total_accessed > 100:
            return True

        return False

    def detect_code_injection_attempt(self, ip_address: str, payload: str) -> bool:
        """Detect code injection attempts"""
        suspicious_patterns = [
            'eval(', 'exec(', 'import ', '__import__', 'subprocess',
            'os.system', 'shell=True', '<script', 'javascript:',
            'SELECT * FROM', 'DROP TABLE', 'UNION SELECT',
            '../../', '../etc/passwd', '/etc/shadow'
        ]

        for pattern in suspicious_patterns:
            if pattern.lower() in payload.lower():
                current_time = time.time()
                self.code_execution_attempts[ip_address].append(current_time)
                return True

        return False

class SecurityMonitor:
    """Main security monitoring system"""

    def __init__(self):
        self.hipaa_logger = HIPAALogger()
        self.threat_detector = ThreatDetector()
        self.active_alerts = {}

    def log_authentication_attempt(self, success: bool, user_id: Optional[str] = None,
                                  ip_address: Optional[str] = None,
                                  user_agent: Optional[str] = None,
                                  failure_reason: Optional[str] = None):
        """Log authentication attempts"""

        if success:
            self.hipaa_logger.log_event(
                SecurityEventType.AUTHENTICATION_SUCCESS,
                SecuritySeverity.LOW,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                action="login",
                details={"method": "JWT"}
            )
        else:
            # Check for brute force
            is_brute_force = False
            if ip_address:
                is_brute_force = self.threat_detector.detect_brute_force(ip_address, user_id)

            severity = SecuritySeverity.CRITICAL if is_brute_force else SecuritySeverity.MEDIUM

            self.hipaa_logger.log_event(
                SecurityEventType.AUTHENTICATION_FAILURE,
                severity,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                action="login_failed",
                details={
                    "failure_reason": failure_reason,
                    "brute_force_detected": is_brute_force
                }
            )

            if is_brute_force:
                self._raise_alert(f"Brute force attack detected from IP: {ip_address}")

    def log_phi_access(self, user_id: str, tenant_id: str, resource: str,
                       action: str, record_count: int = 1,
                       ip_address: Optional[str] = None,
                       patient_ids: Optional[List[str]] = None):
        """Log PHI data access for HIPAA compliance"""

        # Check for suspicious access patterns
        suspicious_access = self.threat_detector.detect_suspicious_phi_access(user_id, record_count)

        severity = SecuritySeverity.HIGH if suspicious_access else SecuritySeverity.MEDIUM

        details = {
            "record_count": record_count,
            "suspicious_pattern": suspicious_access
        }

        if patient_ids:
            # Hash patient IDs for privacy
            details["patient_id_hashes"] = [
                hashlib.sha256(pid.encode()).hexdigest()[:16] for pid in patient_ids
            ]

        event_id = self.hipaa_logger.log_event(
            SecurityEventType.PHI_ACCESS,
            severity,
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            resource=resource,
            action=action,
            details=details,
            phi_accessed=True
        )

        if suspicious_access:
            self._raise_alert(f"Suspicious PHI access pattern detected for user: {user_id}")

        return event_id

    def log_hl7_processing(self, vendor_id: str, message_size: int, message_type: str,
                          processing_time: float, success: bool,
                          ip_address: Optional[str] = None,
                          error_details: Optional[str] = None):
        """Log HL7 message processing"""

        severity = SecuritySeverity.LOW if success else SecuritySeverity.MEDIUM

        self.hipaa_logger.log_event(
            SecurityEventType.HL7_MESSAGE_PROCESSED,
            severity,
            resource=f"hl7_message_{message_type}",
            action="process",
            ip_address=ip_address,
            details={
                "vendor_id": vendor_id,
                "message_size": message_size,
                "message_type": message_type,
                "processing_time_ms": processing_time,
                "success": success,
                "error_details": error_details
            },
            phi_accessed=True  # HL7 messages typically contain PHI
        )

    def log_code_execution(self, user_id: str, tenant_id: str, code_snippet: str,
                          execution_time: float, success: bool,
                          ip_address: Optional[str] = None,
                          error_details: Optional[str] = None):
        """Log custom code execution in workflows"""

        # Check for injection attempts
        is_malicious = self.threat_detector.detect_code_injection_attempt(ip_address or "", code_snippet)

        severity = SecuritySeverity.CRITICAL if is_malicious else SecuritySeverity.MEDIUM

        # Hash the code snippet to avoid logging sensitive content
        code_hash = hashlib.sha256(code_snippet.encode()).hexdigest()

        self.hipaa_logger.log_event(
            SecurityEventType.CODE_EXECUTION,
            severity,
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            resource="workflow_script",
            action="execute",
            details={
                "code_hash": code_hash,
                "code_length": len(code_snippet),
                "execution_time_ms": execution_time,
                "success": success,
                "malicious_detected": is_malicious,
                "error_details": error_details
            }
        )

        if is_malicious:
            self._raise_alert(f"Malicious code execution attempt by user: {user_id}")

    def log_admin_action(self, user_id: str, tenant_id: str, action: str,
                        target_resource: str, ip_address: Optional[str] = None,
                        details: Optional[Dict[str, Any]] = None):
        """Log administrative actions"""

        self.hipaa_logger.log_event(
            SecurityEventType.ADMIN_ACTION,
            SecuritySeverity.HIGH,
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            resource=target_resource,
            action=action,
            details=details or {}
        )

    def log_security_violation(self, violation_type: str, severity: SecuritySeverity,
                              user_id: Optional[str] = None,
                              ip_address: Optional[str] = None,
                              details: Optional[Dict[str, Any]] = None):
        """Log security policy violations"""

        self.hipaa_logger.log_event(
            SecurityEventType.SECURITY_VIOLATION,
            severity,
            user_id=user_id,
            ip_address=ip_address,
            resource="security_policy",
            action="violation",
            details={
                "violation_type": violation_type,
                **(details or {})
            }
        )

        if severity in [SecuritySeverity.HIGH, SecuritySeverity.CRITICAL]:
            self._raise_alert(f"Security violation: {violation_type}")

    def _raise_alert(self, message: str):
        """Raise security alert (integrate with monitoring systems)"""
        alert_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc)

        alert = {
            "id": alert_id,
            "timestamp": timestamp.isoformat(),
            "message": message,
            "status": "active"
        }

        self.active_alerts[alert_id] = alert

        # Log the alert
        logging.getLogger("security_alerts").critical(
            f"SECURITY_ALERT: {alert_id} | {message}"
        )

        # TODO: Integrate with external alerting systems (email, Slack, PagerDuty)

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """Get all active security alerts"""
        return list(self.active_alerts.values())

    def resolve_alert(self, alert_id: str):
        """Mark security alert as resolved"""
        if alert_id in self.active_alerts:
            self.active_alerts[alert_id]["status"] = "resolved"
            self.active_alerts[alert_id]["resolved_at"] = datetime.now(timezone.utc).isoformat()

# Global security monitor instance
security_monitor = SecurityMonitor()