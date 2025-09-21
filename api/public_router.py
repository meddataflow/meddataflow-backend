"""
Public router to expose non-sensitive tenant auth metadata for login screens
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, Query, HTTPException
from pathlib import Path
import json
import uuid

from models.tenant import TenantRepository
from models.user import UserRepository, UserRole
from services.email_service import send_email
from services.auth_service import AuthService

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/tenant-auth")
async def get_tenant_auth_meta(slug: Optional[str] = Query(None), tenant_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Return public auth metadata for a tenant by slug or id.
    Includes: name, slug, sso_enabled, minimal SAML/OAuth config needed for login buttons (no secrets).
    """
    tenant: Optional[Dict[str, Any]] = None
    if slug:
        tenant = await TenantRepository.get_tenant_by_slug(slug)
    elif tenant_id:
        tid = uuid.UUID(tenant_id)
        tenant = await TenantRepository.get_tenant_by_id(tid)
    if not tenant:
        return {"found": False}

    # Normalize configs
    saml_cfg = tenant.get("saml_config") or {}
    oauth_cfg = tenant.get("oauth_config") or {}

    def _saml_public(cfg: Dict[str, Any]) -> Dict[str, Any]:
        # expose only non-sensitive fields
        return {
            "idp_sso_url": cfg.get("idp_sso_url") or cfg.get("sso_url") or cfg.get("login_url"),
            "entity_id": cfg.get("sp_entity_id") or cfg.get("entity_id"),
        }

    def _oauth_public(cfg: Dict[str, Any]) -> Dict[str, Any]:
        # cfg may contain providers like { google: { auth_url }, microsoft: { auth_url } }
        result: Dict[str, Any] = {}
        for k, v in (cfg.items() if isinstance(cfg, dict) else []):
            if isinstance(v, dict):
                result[k] = {"auth_url": v.get("auth_url") or v.get("authorization_endpoint")}
        return result

    return {
        "found": True,
        "name": tenant.get("name"),
        "slug": tenant.get("slug"),
        "sso_enabled": bool(tenant.get("sso_enabled", False)),
        "saml": _saml_public(saml_cfg) if tenant.get("sso_enabled") and saml_cfg else None,
        "oauth": _oauth_public(oauth_cfg) if tenant.get("sso_enabled") and oauth_cfg else None,
    }


@router.post("/tenant-signup")
async def tenant_signup(body: Dict[str, Any]):
    """Public endpoint to request a new tenant.
    Creates a tenant with is_active = false and an initial TENANT_ADMIN user.
    """
    required = ["name", "slug", "admin_email", "admin_password"]
    if not all(k in body and body[k] for k in required):
        raise HTTPException(status_code=400, detail="Missing required fields")
    name = body["name"].strip()
    slug = body["slug"].strip()
    admin_email = body["admin_email"].strip().lower()
    admin_password = body["admin_password"]

    # Validate slug availability
    if not await TenantRepository.validate_slug(slug):
        raise HTTPException(status_code=400, detail="Tenant slug already exists")

    # Create tenant (inactive by default)
    tenant = await TenantRepository.create_tenant(name=name, slug=slug)
    tid: uuid.UUID = tenant['id'] if isinstance(tenant['id'], uuid.UUID) else uuid.UUID(str(tenant['id']))
    # Immediately mark inactive
    await TenantRepository.update_tenant(tid, is_active=False)

    # Check existing user email globally
    existing_global = await UserRepository.get_user_by_email(admin_email)
    if existing_global:
        raise HTTPException(status_code=400, detail="Email already in use")
    # Create initial tenant admin user
    user = await UserRepository.create_user(
        email=admin_email,
        password=admin_password,
        first_name=admin_email.split('@')[0],
        last_name="",
        tenant_id=tid,
        role=UserRole.TENANT_ADMIN
    )
    # Issue access token so the admin can proceed to choose plan
    claims = {
        'sub': str(user['id']),
        'email': user['email'],
        'role': user['role'],
        'tenant_id': str(tid),
    }
    token = AuthService.create_access_token(claims)
    return {
        "requested": True,
        "tenant_id": str(tid),
        "tenant_slug": slug,
        "tenant_active": False,
        "admin_user_id": str(user['id']),
        "access_token": token,
    }


@router.post("/provision")
async def provision(body: Dict[str, Any]):
    """Provision tenant + initial admin user at the final step of signup.

    Expected body: { name, slug, admin_email, admin_password, plan?, activation: 'admin_approval'|'active' }
    - admin_approval: tenant inactive, user inactive, billing pending_approval, notifies admins
    - active: tenant active, user active
    """
    required = ["name", "slug", "admin_email", "admin_password", "activation"]
    if not all(k in body and body[k] for k in required):
        raise HTTPException(status_code=400, detail="Missing required fields")
    name = str(body["name"]).strip()
    slug = str(body["slug"]).strip()
    admin_email = str(body["admin_email"]).strip().lower()
    admin_password = body["admin_password"]
    activation = str(body["activation"]).strip().lower()
    plan = str(body.get("plan") or "PROFESSIONAL").upper()
    # Accept short codes and normalize
    if plan in {"PRO", "PROF"}:
        plan = "PROFESSIONAL"

    # Validate slug and email availability
    if not await TenantRepository.validate_slug(slug):
        raise HTTPException(status_code=400, detail="Tenant slug already exists")
    existing_global = await UserRepository.get_user_by_email(admin_email)
    if existing_global:
        raise HTTPException(status_code=400, detail="Email already in use")

    # Create tenant active by default, adjust by activation
    tenant = await TenantRepository.create_tenant(name=name, slug=slug, plan=plan)
    tid = tenant['id'] if isinstance(tenant['id'], uuid.UUID) else uuid.UUID(str(tenant['id']))

    # Create admin user
    user = await UserRepository.create_user(
        email=admin_email,
        password=admin_password,
        first_name=admin_email.split('@')[0],
        last_name="",
        tenant_id=tid,
        role=UserRole.TENANT_ADMIN
    )

    # Handle activation mode
    import json as _json
    settings = tenant.get('settings') or {}
    if isinstance(settings, str):
        try:
            settings = _json.loads(settings) or {}
        except Exception:
            settings = {}
    billing = dict(settings.get('billing') or {})

    if activation == 'admin_approval':
        await TenantRepository.update_tenant(tid, is_active=False)
        # Deactivate the new user account until approval
        try:
            await UserRepository.update_user(user['id'], is_active=False)
        except Exception:
            pass
        billing['billing_exempt'] = True
        billing['subscription_status'] = 'pending_approval'
        settings['billing'] = billing
        await TenantRepository.update_tenant(tid, settings=settings)
        # Notify super admins and requester
        try:
            from models.user import UserRepository as UR
            supers = await UR.list_super_admin_emails()
            subj = f"Tenant awaiting approval: {name}"
            body_txt = f"Tenant '{name}' (slug: {slug}) requested activation without payment.\n\nLogin to Admin > Approvals to review."
            for addr in supers:
                send_email(addr, subj, body_txt)
            if tenant.get('billing_email'):
                send_email(tenant['billing_email'], "Your activation request is pending", "We received your request. A MedDataFlow admin will review and activate your tenant.")
        except Exception:
            pass
        return {"provisioned": True, "tenant_id": str(tid), "status": "pending_approval"}

    elif activation == 'active':
        # Immediately activate tenant and user
        await TenantRepository.update_tenant(tid, is_active=True)
        try:
            await UserRepository.update_user(user['id'], is_active=True)
        except Exception:
            pass
        billing['subscription_status'] = 'active'
        settings['billing'] = billing
        await TenantRepository.update_tenant(tid, settings=settings)
        return {"provisioned": True, "tenant_id": str(tid), "status": "active"}

    else:
        raise HTTPException(status_code=400, detail="Invalid activation mode")


# Public platform branding (no auth)
BRANDING_PATH = Path(__file__).resolve().parent.parent / "config" / "branding.json"

def _read_platform_branding() -> Dict[str, Any]:
    try:
        if BRANDING_PATH.exists():
            data = json.loads(BRANDING_PATH.read_text())
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {
        "app_name": "meddataflow",
        "company_logo_url": None,
        "favicon_url": None,
        "idle_timeout_minutes": 5,
        "idle_warning_seconds": 60,
    }

@router.get("/platform-branding")
async def public_platform_branding() -> Dict[str, Any]:
    """Publicly readable branding for unauthenticated pages (login, landing)."""
    return _read_platform_branding()


@router.post("/contact")
async def contact_us(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Public contact endpoint to send inquiries to the platform team.

    Expected JSON: { name: str, email: str, message: str }
    Sends an email to the support inbox and returns a generic success response.
    """
    try:
        name = str(payload.get("name") or "").strip()
        email = str(payload.get("email") or "").strip()
        message = str(payload.get("message") or "").strip()

        if not name or not email or not message:
            raise HTTPException(status_code=400, detail="All fields are required")

        # Basic length limits to avoid abuse
        if len(name) > 120 or len(email) > 200 or len(message) > 5000:
            raise HTTPException(status_code=400, detail="Input too long")

        subject = f"[Contact] Message from {name}"
        body = f"Name: {name}\nEmail: {email}\n\nMessage:\n{message}"

        # Destination inbox
        to_addr = "info@meddataflow.com"
        sent = send_email(to_addr, subject, body)

        return {"ok": True, "sent": sent}
    except HTTPException:
        raise
    except Exception as e:
        # Avoid leaking details
        return {"ok": False}
