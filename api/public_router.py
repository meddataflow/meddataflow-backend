"""
Public router to expose non-sensitive tenant auth metadata for login screens
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, Query, HTTPException
import os
import httpx
from pathlib import Path
import json
import uuid

from models.tenant import TenantRepository
from models.user import UserRepository, UserRole
from services.email_service import send_email
from services.auth_service import AuthService
from services.settings_service import settings_service

router = APIRouter(prefix="/api/public", tags=["public"])
# Allow either RECAPTCHA_SECRET_KEY or GOOGLE_RECAPTCHA_SECRET; fall back to provided key for local dev
RECAPTCHA_SECRET = (
    os.getenv("RECAPTCHA_SECRET_KEY")
    or os.getenv("GOOGLE_RECAPTCHA_SECRET")
    or "6LcmpC0sAAAAAAK6aNW8Rg0SRbAaOL1dyFKe8KI-"
)
RECAPTCHA_MIN_SCORE = float(os.getenv("RECAPTCHA_MIN_SCORE", "0.4"))


async def verify_recaptcha(token: str, action: str = "tenant_signup"):
    if not token:
        raise HTTPException(status_code=400, detail="Missing reCAPTCHA token")
    if not RECAPTCHA_SECRET:
        raise HTTPException(status_code=500, detail="reCAPTCHA secret is not configured on the server")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            res = await client.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={"secret": RECAPTCHA_SECRET, "response": token}
            )
        data = res.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Unable to verify reCAPTCHA")
    success = data.get("success")
    score = data.get("score", 0)
    action_resp = data.get("action")
    if not success or score < RECAPTCHA_MIN_SCORE or (action_resp and action_resp != action):
        raise HTTPException(status_code=400, detail="reCAPTCHA verification failed")


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
    recaptcha_token = body.get("recaptcha_token")
    industry = (body.get("industry") or "").strip() or None
    team_size = (body.get("team_size") or "").strip() or None
    primary_use_case = (body.get("primary_use_case") or "").strip() or None
    ehr_vendor = (body.get("ehr_vendor") or "").strip() or None
    region = (body.get("region") or "").strip() or None
    security_contact = (body.get("security_contact") or "").strip() or None
    onboarding_notes = (body.get("notes") or "").strip() or None

    # Verify reCAPTCHA
    await verify_recaptcha(recaptcha_token, action="tenant_signup")

    # Validate slug availability
    if not await TenantRepository.validate_slug(slug):
        raise HTTPException(status_code=400, detail="Tenant slug already exists")

    # Create tenant (inactive by default)
    tenant = await TenantRepository.create_tenant(
        name=name,
        slug=slug,
        industry=industry,
        team_size=team_size,
        primary_use_case=primary_use_case,
        ehr_vendor=ehr_vendor,
        region=region,
        security_contact=security_contact,
        onboarding_notes=onboarding_notes
    )
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
    industry = (body.get("industry") or "").strip() or None
    team_size = (body.get("team_size") or "").strip() or None
    primary_use_case = (body.get("primary_use_case") or "").strip() or None
    ehr_vendor = (body.get("ehr_vendor") or "").strip() or None
    region = (body.get("region") or "").strip() or None
    security_contact = (body.get("security_contact") or "").strip() or None
    onboarding_notes = (body.get("notes") or "").strip() or None
    recaptcha_token = body.get("recaptcha_token")
    activation = str(body["activation"]).strip().lower()
    plan = str(body.get("plan") or "PROFESSIONAL").upper()
    # Accept short codes and normalize
    if plan in {"PRO", "PROF"}:
        plan = "PROFESSIONAL"

    if recaptcha_token:
        await verify_recaptcha(recaptcha_token, action="tenant_signup")

    # Validate slug and email availability
    if not await TenantRepository.validate_slug(slug):
        raise HTTPException(status_code=400, detail="Tenant slug already exists")
    existing_global = await UserRepository.get_user_by_email(admin_email)
    if existing_global:
        raise HTTPException(status_code=400, detail="Email already in use")

    # Create tenant active by default, adjust by activation
    tenant = await TenantRepository.create_tenant(
        name=name,
        slug=slug,
        plan=plan,
        industry=industry,
        team_size=team_size,
        primary_use_case=primary_use_case,
        ehr_vendor=ehr_vendor,
        region=region,
        security_contact=security_contact,
        onboarding_notes=onboarding_notes
    )
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

        # Create notification entry for admin dashboard
        try:
            from pathlib import Path
            from datetime import datetime
            notif_path = Path(__file__).resolve().parent.parent / 'config' / 'notifications.json'
            notif_path.parent.mkdir(parents=True, exist_ok=True)

            existing = []
            if notif_path.exists():
                try:
                    existing = _json.loads(notif_path.read_text()) or []
                except Exception:
                    existing = []

            # Add new notification
            new_notif = {
                "type": "TENANT_PENDING_APPROVAL",
                "tenant_id": str(tid),
                "tenant_name": name,
                "tenant_slug": slug,
                "timestamp": datetime.utcnow().isoformat() + 'Z',
                "message": f"Tenant '{name}' is awaiting admin approval"
            }
            existing.append(new_notif)
            notif_path.write_text(_json.dumps(existing, indent=2))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to create notification entry: {e}")

        # Notify super admins and requester via email
        try:
            from models.user import UserRepository as UR
            supers = await UR.list_super_admin_emails()
            subj = f"Tenant awaiting approval: {name}"
            body_txt = f"Tenant '{name}' (slug: {slug}) requested activation without payment.\n\nLogin to Admin > Approvals to review."
            for addr in supers:
                await send_email(addr, subj, body_txt)

            # Send confirmation to admin user
            await send_email(
                admin_email,
                "Your tenant activation request is pending",
                f"Thank you for registering '{name}'.\n\nYour request is pending approval from our team. You will receive an email when your account is activated.",
                f"<p>Thank you for registering '<strong>{name}</strong>'.</p><p>Your request is pending approval from our team. You will receive an email when your account is activated.</p>"
            )

            # Also send to billing email if provided and different
            if tenant.get('billing_email') and tenant.get('billing_email') != admin_email:
                await send_email(tenant['billing_email'], "Your activation request is pending", "We received your request. A MedDataFlow admin will review and activate your tenant.")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to send approval notification emails: {e}")

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


@router.get("/platform-branding")
async def public_platform_branding() -> Dict[str, Any]:
    """Publicly readable branding for unauthenticated pages (login, landing)."""
    return await settings_service.get_platform_branding()


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
        sent = await send_email(to_addr, subject, body)

        return {"ok": True, "sent": sent}
    except HTTPException:
        raise
    except Exception as e:
        # Avoid leaking details
        return {"ok": False}
