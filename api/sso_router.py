"""
Simplified SSO flow endpoints (initiate + callback) to support demo integration.
In production, integrate with real IdPs and OAuth providers.
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
import uuid

from models.tenant import TenantRepository
from models.user import UserRepository, UserRole
from services.auth_service import AuthService

router = APIRouter(prefix="/api/auth/sso", tags=["sso"])


@router.get("/initiate")
async def sso_initiate(tenant_slug: str = Query(...), provider: str = Query(...)):
    """Initiate SSO. For demo purposes, redirect to callback with mock params.
    If tenant has configured SAML/OAuth URLs, you could redirect there instead.
    """
    tenant = await TenantRepository.get_tenant_by_slug(tenant_slug)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # In a real flow, redirect to IdP or OAuth provider. Here we simulate by
    # redirecting to frontend callback with only the tenant_slug & provider.
    frontend = "http://localhost:3000"
    # Optional: pass along email hint via query (?email=)
    return RedirectResponse(url=f"{frontend}/auth/sso/callback?tenant={tenant_slug}&provider={provider}")


@router.get("/callback")
async def sso_callback(tenant_slug: str = Query(...), provider: str = Query(...), email: Optional[str] = Query(None)):
    """Handle SSO callback (demo).
    - Looks up tenant by slug
    - If email provided, finds or creates user in that tenant
    - Issues an access token and redirects to frontend callback with token
    """
    tenant = await TenantRepository.get_tenant_by_slug(tenant_slug)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant_id: uuid.UUID = tenant['id'] if isinstance(tenant['id'], uuid.UUID) else uuid.UUID(str(tenant['id']))

    # For demo: require email to identify/create user
    if not email:
        # Redirect frontend to ask for email entry then call back with email
        frontend = "http://localhost:3000"
        return RedirectResponse(url=f"{frontend}/auth/sso/callback?tenant={tenant_slug}&provider={provider}")

    # Find or create user
    user = await UserRepository.get_user_by_email(email, tenant_id)
    if not user:
        # Create a viewer by default
        user = await UserRepository.create_user(
            email=email,
            password="",  # no local password
            first_name=email.split('@')[0],
            last_name="",
            tenant_id=tenant_id,
            role=UserRole.VIEWER
        )

    claims: Dict[str, Any] = {
        'sub': str(user['id'] if isinstance(user['id'], uuid.UUID) else user['id']),
        'email': user['email'],
        'role': user['role'],
        'tenant_id': str(tenant_id),
        'provider': provider,
    }
    token = AuthService.create_access_token(claims)

    frontend = "http://localhost:3000"
    return RedirectResponse(url=f"{frontend}/auth/sso/callback?tenant={tenant_slug}&provider={provider}&token={token}")

