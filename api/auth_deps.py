"""
Authentication dependencies for FastAPI endpoints
"""
from typing import Optional, Dict, Any
import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from services.auth_service import AuthService, TenantService
from models.tenant import TenantRepository

# Security scheme
security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """Get current authenticated user from JWT token"""
    user = await AuthService.get_current_user_from_token(credentials.credentials)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return user

async def get_current_tenant(
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get effective tenant for current user.
    - Uses user's tenant_id when present.
    - Falls back to impersonation context (impersonate_tenant_id) for super admins.
    """
    tenant_id = current_user.get('tenant_id') or current_user.get('impersonate_tenant_id')
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not associated with any tenant"
        )

    # Normalize to UUID and load tenant
    tenant_uuid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    tenant = await TenantRepository.get_tenant_by_id(tenant_uuid)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    return tenant

async def get_current_tenant_allow_inactive(
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get tenant for current user, allowing inactive tenants (for onboarding/checkout)."""
    tenant_id = current_user.get('tenant_id') or current_user.get('impersonate_tenant_id')
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not associated with any tenant"
        )
    tenant_uuid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    tenant = await TenantRepository.get_tenant_by_id_any_status(tenant_uuid)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    return tenant

async def verify_api_key(api_key: str) -> Dict[str, Any]:
    """Verify API key and return associated tenant"""
    tenant = await TenantService.get_tenant_by_api_key(api_key)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return tenant

def require_roles(allowed_roles: list):
    """Create a dependency that requires specific user roles"""
    def role_checker(current_user: Dict[str, Any] = Depends(get_current_user)):
        user_role = current_user.get('role')
        if not user_role or not AuthService.check_permission(user_role, allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return role_checker

def require_tenant_admin():
    """Require tenant admin role or higher"""
    from models.user import UserRole
    return require_roles([UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN])

def require_workflow_admin():
    """Require workflow admin role or higher"""
    from models.user import UserRole
    return require_roles([UserRole.WORKFLOW_ADMIN, UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN])

def require_analyst():
    """Require analyst role or higher"""
    from models.user import UserRole
    return require_roles([UserRole.ANALYST, UserRole.WORKFLOW_ADMIN, UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN])

def require_super_admin():
    """Require super admin role"""
    from models.user import UserRole
    return require_roles([UserRole.SUPER_ADMIN])
