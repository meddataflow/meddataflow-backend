"""
Admin Audit Log API Routes
Provides endpoints for logging and retrieving audit trail information
"""
import uuid
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from models.audit_log import AuditLogRepository
from api.auth_deps import require_super_admin

router = APIRouter(prefix="/api/admin", tags=["Admin", "Audit"])

# Simple test endpoint to verify router is working
@router.get("/audit-log/test")
async def test_audit_endpoint():
    """Test endpoint to verify router registration"""
    return {"message": "Audit log router is working"}


class CreateAuditLogRequest(BaseModel):
    action: str = Field(..., max_length=100, description="Action performed")
    resource_type: Optional[str] = Field(None, max_length=50, description="Type of resource affected")
    resource_id: Optional[str] = Field(None, max_length=100, description="ID of affected resource")
    tenant_id: Optional[uuid.UUID] = Field(None, description="Tenant ID if applicable")
    metadata: Optional[dict] = Field(None, description="Additional context data")
    status: str = Field("SUCCESS", max_length=20, description="Action status")
    error_message: Optional[str] = Field(None, description="Error message if failed")

    # Frontend-specific fields for impersonation
    target_tenant_id: Optional[str] = Field(None, description="Target tenant ID for impersonation")
    timestamp: Optional[str] = Field(None, description="Frontend timestamp")
    user_agent: Optional[str] = Field(None, description="User agent from frontend")
    ip_address: Optional[str] = Field(None, description="IP address from frontend")


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    user_email: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    timestamp: datetime
    ip_address: Optional[str]
    user_agent: Optional[str]
    tenant_id: Optional[uuid.UUID]
    session_id: Optional[str]
    metadata: Optional[dict]
    status: str
    error_message: Optional[str]



@router.post("/audit-log", response_model=AuditLogResponse)
async def create_audit_log(
    request: CreateAuditLogRequest,
    http_request: Request,
    current_user = Depends(require_super_admin())
):
    """Create a new audit log entry"""

    # Extract request context - prefer frontend values if provided
    ip_address = request.ip_address or (http_request.client.host if http_request.client else None)
    user_agent = request.user_agent or http_request.headers.get("user-agent")
    session_id = http_request.cookies.get("session_id")

    # Handle impersonation-specific fields
    metadata = request.metadata or {}
    if request.target_tenant_id:
        metadata['target_tenant_id'] = request.target_tenant_id
        # For impersonation actions, set resource info
        if not request.resource_type:
            request.resource_type = 'tenant'
        if not request.resource_id:
            request.resource_id = request.target_tenant_id

    try:
        audit_log = await AuditLogRepository.create_audit_log(
            action=request.action,
            user_id=current_user.id,
            user_email=current_user.email,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            tenant_id=request.tenant_id,
            session_id=session_id,
            metadata=metadata,
            status=request.status,
            error_message=request.error_message
        )

        return AuditLogResponse(**audit_log)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create audit log: {str(e)}")


@router.get("/audit-log", response_model=List[AuditLogResponse])
async def get_audit_logs(
    user_id: Optional[uuid.UUID] = None,
    tenant_id: Optional[uuid.UUID] = None,
    action: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
    current_user = Depends(require_super_admin())
):
    """Retrieve audit logs with filtering"""

    try:
        audit_logs = await AuditLogRepository.get_audit_logs(
            user_id=user_id,
            tenant_id=tenant_id,
            action=action,
            start_date=start_date,
            end_date=end_date,
            limit=min(limit, 1000),  # Cap at 1000 for performance
            offset=offset
        )

        return [AuditLogResponse(**log) for log in audit_logs]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve audit logs: {str(e)}")


@router.get("/audit-log/impersonation", response_model=List[AuditLogResponse])
async def get_impersonation_logs(
    admin_user_id: Optional[uuid.UUID] = None,
    target_tenant_id: Optional[uuid.UUID] = None,
    limit: int = 50,
    current_user = Depends(require_super_admin())
):
    """Get impersonation-specific audit logs"""

    try:
        audit_logs = await AuditLogRepository.get_impersonation_logs(
            admin_user_id=admin_user_id,
            target_tenant_id=target_tenant_id,
            limit=min(limit, 500)  # Cap at 500 for performance
        )

        return [AuditLogResponse(**log) for log in audit_logs]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve impersonation logs: {str(e)}")


@router.post("/audit-log/impersonation/start", response_model=AuditLogResponse)
async def log_impersonation_start(
    target_tenant_id: uuid.UUID,
    http_request: Request,
    current_user = Depends(require_super_admin())
):
    """Log the start of tenant impersonation"""

    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")
    session_id = http_request.cookies.get("session_id")

    try:
        audit_log = await AuditLogRepository.log_impersonation_start(
            admin_user_id=current_user.id,
            admin_email=current_user.email,
            target_tenant_id=target_tenant_id,
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_id
        )

        return AuditLogResponse(**audit_log)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to log impersonation start: {str(e)}")


@router.post("/audit-log/impersonation/end", response_model=AuditLogResponse)
async def log_impersonation_end(
    http_request: Request,
    current_user = Depends(require_super_admin())
):
    """Log the end of tenant impersonation"""

    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")
    session_id = http_request.cookies.get("session_id")

    try:
        audit_log = await AuditLogRepository.log_impersonation_end(
            admin_user_id=current_user.id,
            admin_email=current_user.email,
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_id
        )

        return AuditLogResponse(**audit_log)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to log impersonation end: {str(e)}")


@router.post("/audit-log/security-event", response_model=AuditLogResponse)
async def log_security_event(
    event_type: str,
    http_request: Request,
    details: Optional[dict] = None,
    status: str = "SUCCESS",
    current_user = Depends(require_super_admin())
):
    """Log a security event"""

    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")

    try:
        audit_log = await AuditLogRepository.log_security_event(
            event_type=event_type,
            user_id=current_user.id,
            user_email=current_user.email,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
            status=status
        )

        return AuditLogResponse(**audit_log)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to log security event: {str(e)}")