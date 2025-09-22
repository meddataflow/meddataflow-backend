"""
Audit Log Model for HIPAA Compliance
Tracks all security-sensitive actions including impersonation
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from database.connection import execute_returning, fetch_all, execute
import json


class AuditLogRepository:
    """Repository for audit log operations"""

    @staticmethod
    async def create_audit_log(
        action: str,
        user_id: Optional[uuid.UUID] = None,
        user_email: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        tenant_id: Optional[uuid.UUID] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        status: str = 'SUCCESS',
        error_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new audit log entry"""

        # Sanitize metadata to ensure no PHI is logged
        safe_metadata = {}
        if metadata:
            # Only include safe fields
            safe_fields = [
                'target_tenant_id', 'action_type', 'request_id', 'duration_ms',
                'api_version', 'client_version', 'feature_flags'
            ]
            safe_metadata = {k: v for k, v in metadata.items() if k in safe_fields}

        audit_log_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        query = """
        INSERT INTO audit_logs (
            id, user_id, user_email, action, resource_type, resource_id,
            timestamp, ip_address, user_agent, tenant_id, session_id,
            metadata, status, error_message
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        RETURNING *
        """

        return await execute_returning(
            query,
            audit_log_id, user_id, user_email, action, resource_type, resource_id,
            now, ip_address, user_agent, tenant_id, session_id,
            json.dumps(safe_metadata) if safe_metadata else None,
            status, error_message
        )

    @staticmethod
    async def get_audit_logs(
        user_id: Optional[uuid.UUID] = None,
        tenant_id: Optional[uuid.UUID] = None,
        action: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieve audit logs with filtering"""

        # Build WHERE conditions
        conditions = []
        params = []
        param_count = 0

        if user_id:
            param_count += 1
            conditions.append(f"user_id = ${param_count}")
            params.append(user_id)

        if tenant_id:
            param_count += 1
            conditions.append(f"tenant_id = ${param_count}")
            params.append(tenant_id)

        if action:
            param_count += 1
            conditions.append(f"action = ${param_count}")
            params.append(action)

        if start_date:
            param_count += 1
            conditions.append(f"timestamp >= ${param_count}")
            params.append(start_date)

        if end_date:
            param_count += 1
            conditions.append(f"timestamp <= ${param_count}")
            params.append(end_date)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Add limit and offset
        param_count += 1
        params.append(limit)
        limit_param = f"${param_count}"

        param_count += 1
        params.append(offset)
        offset_param = f"${param_count}"

        query = f"""
        SELECT * FROM audit_logs
        {where_clause}
        ORDER BY timestamp DESC
        LIMIT {limit_param} OFFSET {offset_param}
        """

        return await fetch_all(query, *params)

    @staticmethod
    async def get_impersonation_logs(
        admin_user_id: Optional[uuid.UUID] = None,
        target_tenant_id: Optional[uuid.UUID] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get impersonation-specific logs"""

        conditions = ["(action = 'IMPERSONATE_TENANT' OR action = 'EXIT_IMPERSONATION')"]
        params = []
        param_count = 0

        if admin_user_id:
            param_count += 1
            conditions.append(f"user_id = ${param_count}")
            params.append(admin_user_id)

        if target_tenant_id:
            param_count += 1
            conditions.append(f"metadata->>'target_tenant_id' = ${param_count}")
            params.append(str(target_tenant_id))

        where_clause = "WHERE " + " AND ".join(conditions)

        param_count += 1
        params.append(limit)
        limit_param = f"${param_count}"

        query = f"""
        SELECT * FROM audit_logs
        {where_clause}
        ORDER BY timestamp DESC
        LIMIT {limit_param}
        """

        return await fetch_all(query, *params)

    @staticmethod
    async def log_impersonation_start(
        admin_user_id: uuid.UUID,
        admin_email: str,
        target_tenant_id: uuid.UUID,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Log the start of impersonation"""

        return await AuditLogRepository.create_audit_log(
            action='IMPERSONATE_TENANT',
            user_id=admin_user_id,
            user_email=admin_email,
            resource_type='tenant',
            resource_id=str(target_tenant_id),
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_id,
            metadata={
                'target_tenant_id': str(target_tenant_id),
                'action_type': 'admin_impersonation'
            }
        )

    @staticmethod
    async def log_impersonation_end(
        admin_user_id: uuid.UUID,
        admin_email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Log the end of impersonation"""

        return await AuditLogRepository.create_audit_log(
            action='EXIT_IMPERSONATION',
            user_id=admin_user_id,
            user_email=admin_email,
            resource_type='session',
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_id,
            metadata={
                'action_type': 'admin_impersonation_end'
            }
        )

    @staticmethod
    async def log_security_event(
        event_type: str,
        user_id: Optional[uuid.UUID] = None,
        user_email: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: str = 'SUCCESS'
    ) -> Dict[str, Any]:
        """Log general security events"""

        return await AuditLogRepository.create_audit_log(
            action=f'SECURITY_{event_type}',
            user_id=user_id,
            user_email=user_email,
            resource_type='security',
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=details,
            status=status
        )