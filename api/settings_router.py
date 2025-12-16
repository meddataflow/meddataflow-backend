"""
Settings router: user and tenant settings management
"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from pydantic import BaseModel, EmailStr
import uuid
import os
from pathlib import Path
import json
import base64

from api.auth_deps import get_current_user, get_current_tenant, require_tenant_admin, require_super_admin
from models.user import UserRepository
from models.tenant import TenantRepository
from services.settings_service import settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UserSettings(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    timezone: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class TenantSettings(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    billing_email: Optional[EmailStr] = None
    plan: Optional[str] = None
    sso_enabled: Optional[bool] = None
    saml_config: Optional[Dict[str, Any]] = None
    oauth_config: Optional[Dict[str, Any]] = None
    # Security and system knobs captured in settings JSON
    security: Optional[Dict[str, Any]] = None  # {enforce_mfa, password_policy, session_timeout}
    integrations: Optional[Dict[str, Any]] = None  # {webhooks_enabled, api_rate_limit}
    retention_days: Optional[int] = None
    limits: Optional[Dict[str, Any]] = None  # {max_users, max_workflows}
    branding: Optional[Dict[str, Any]] = None  # {tenant_logo_url}
    settings: Optional[Dict[str, Any]] = None  # raw passthrough
    billing: Optional[Dict[str, Any]] = None   # { subscription_status, billing_exempt, customer_id, subscription_id }


class PlatformBranding(BaseModel):
    app_name: Optional[str] = None
    company_logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    idle_timeout_minutes: Optional[int] = None
    idle_warning_seconds: Optional[int] = None


class MLLPConfig(BaseModel):
    enabled: Optional[bool] = None
    host: Optional[str] = None
    port: Optional[int] = None
    ack_mode: Optional[str] = None  # none|auto|accept
    tenant_id: Optional[str] = None
    tenant_slug: Optional[str] = None
    vendor_slug: Optional[str] = None
    tls_enabled: Optional[bool] = None
    tls_cert_file: Optional[str] = None
    tls_key_file: Optional[str] = None
    tls_ca_file: Optional[str] = None
    require_client_cert: Optional[bool] = None


@router.get("/user")
async def get_user_settings(current_user: Dict[str, Any] = Depends(get_current_user)):
    # Return a subset relevant for settings UI
    return {
        "first_name": current_user.get("first_name"),
        "last_name": current_user.get("last_name"),
        "email": current_user.get("email"),
        "timezone": current_user.get("timezone", "UTC"),
        "preferences": current_user.get("preferences", {}),
        "role": current_user.get("role"),
    }


@router.patch("/user")
async def update_user_settings(payload: UserSettings, current_user: Dict[str, Any] = Depends(get_current_user)):
    user_id = current_user["id"] if isinstance(current_user["id"], uuid.UUID) else uuid.UUID(current_user["id"])
    updates: Dict[str, Any] = {}
    if payload.first_name is not None:
        updates["first_name"] = payload.first_name
    if payload.last_name is not None:
        updates["last_name"] = payload.last_name
    if payload.timezone is not None:
        updates["timezone"] = payload.timezone
    if payload.preferences is not None:
        updates["preferences"] = payload.preferences
    if not updates:
        return {"updated": False}
    updated = await UserRepository.update_user(user_id, **updates)
    return {"updated": True, "user": {"first_name": updated["first_name"], "last_name": updated["last_name"], "timezone": updated.get("timezone"), "preferences": updated.get("preferences", {})}}


@router.post("/change-password")
async def change_password(payload: ChangePasswordRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    # Verify current password if hash exists
    if not current_user.get("password_hash"):
        raise HTTPException(status_code=400, detail="Password change not available for SSO accounts")
    valid = await UserRepository.verify_password(payload.current_password, current_user["password_hash"])
    if not valid:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user_id = current_user["id"] if isinstance(current_user["id"], uuid.UUID) else uuid.UUID(current_user["id"])
    await UserRepository.update_password(user_id, payload.new_password)
    return {"changed": True}


@router.get("/tenant")
async def get_tenant_settings(
    current_user: Dict[str, Any] = Depends(get_current_user),
    tenant_id: Optional[str] = Query(None)
):
    # Super admin: explicit tenant_id supported without tenant context
    tenant: Optional[Dict[str, Any]] = None
    if tenant_id and current_user.get("role") == "SUPER_ADMIN":
        try:
            tid = uuid.UUID(tenant_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid tenant id")
        tenant = await TenantRepository.get_tenant_by_id(tid)
    else:
        # Resolve from user's effective context (tenant_id or impersonation)
        eff_tid = current_user.get('tenant_id') or current_user.get('impersonate_tenant_id')
        if eff_tid:
            tid = eff_tid if isinstance(eff_tid, uuid.UUID) else uuid.UUID(str(eff_tid))
            tenant = await TenantRepository.get_tenant_by_id(tid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    api_key = tenant.get("api_key")
    api_key_last4 = api_key[-4:] if api_key else None
    # Normalize settings to dict
    raw_settings = tenant.get("settings", {}) or {}
    if isinstance(raw_settings, str):
        try:
            raw_settings = json.loads(raw_settings)
        except Exception:
            raw_settings = {}
    await _restore_tenant_logo_file(tenant["id"] if isinstance(tenant["id"], uuid.UUID) else uuid.UUID(str(tenant["id"])), raw_settings)
    return {
        "id": str(tenant["id"]),
        "name": tenant["name"],
        "slug": tenant["slug"],
        "domain": tenant.get("domain"),
        "plan": tenant.get("plan"),
        "billing_email": tenant.get("billing_email"),
        "sso_enabled": tenant.get("sso_enabled", False),
        "api_key_last4": api_key_last4,
        "has_api_key": bool(api_key),
        "saml_config": tenant.get("saml_config"),
        "oauth_config": tenant.get("oauth_config"),
        "settings": raw_settings,
        "branding": (raw_settings or {}).get("branding"),
    }


@router.patch("/tenant")
async def update_tenant_settings(
    payload: TenantSettings,
    current_user: Dict[str, Any] = Depends(require_tenant_admin()) ,
    tenant_id: Optional[str] = Query(None)
):
    # Resolve effective tenant
    if tenant_id and current_user.get("role") == "SUPER_ADMIN":
        target_tenant_id = uuid.UUID(tenant_id)
    else:
        eff_tid = current_user.get('tenant_id') or current_user.get('impersonate_tenant_id')
        if not eff_tid:
            raise HTTPException(status_code=403, detail="User not associated with any tenant")
        target_tenant_id = eff_tid if isinstance(eff_tid, uuid.UUID) else uuid.UUID(str(eff_tid))

    updates: Dict[str, Any] = {}
    for f in ["name", "domain", "billing_email", "plan", "sso_enabled", "saml_config", "oauth_config"]:
        v = getattr(payload, f)
        if v is not None:
            updates[f] = v

    # Merge structured settings into tenant.settings JSON
    tenant = await TenantRepository.get_tenant_by_id(target_tenant_id)
    # Normalize settings merge source
    current_settings = tenant.get("settings", {}) or {}
    if isinstance(current_settings, str):
        try:
            current_settings = json.loads(current_settings)
        except Exception:
            current_settings = {}
    current_settings = dict(current_settings)
    if payload.security is not None:
        current_settings["security"] = {**current_settings.get("security", {}), **payload.security}
    if payload.integrations is not None:
        current_settings["integrations"] = {**current_settings.get("integrations", {}), **payload.integrations}
    if payload.retention_days is not None:
        current_settings["retention_days"] = payload.retention_days
    if payload.limits is not None:
        current_settings["limits"] = {**current_settings.get("limits", {}), **payload.limits}
    if payload.settings is not None:
        current_settings = {**current_settings, **payload.settings}
    if payload.branding is not None:
        current_settings["branding"] = {**current_settings.get("branding", {}), **payload.branding}
    if payload.billing is not None:
        current_settings["billing"] = {**current_settings.get("billing", {}), **payload.billing}
    updates["settings"] = current_settings

    updated = await TenantRepository.update_tenant(target_tenant_id, **updates)
    if not updated:
        raise HTTPException(status_code=400, detail="No changes applied")
    return {"updated": True, "tenant": {"id": str(updated["id"]), "name": updated["name"], "settings": current_settings}}


@router.post("/tenant/regenerate-api-key")
async def regenerate_tenant_api_key(
    current_user: Dict[str, Any] = Depends(require_tenant_admin()),
    tenant_id: Optional[str] = Query(None)
):
    if tenant_id and current_user.get("role") == "SUPER_ADMIN":
        target_tenant_id = uuid.UUID(tenant_id)
    else:
        eff_tid = current_user.get('tenant_id') or current_user.get('impersonate_tenant_id')
        if not eff_tid:
            raise HTTPException(status_code=403, detail="User not associated with any tenant")
        target_tenant_id = eff_tid if isinstance(eff_tid, uuid.UUID) else uuid.UUID(str(eff_tid))
    updated = await TenantRepository.regenerate_api_key(target_tenant_id)
    return {"api_key": updated.get("api_key"), "id": str(updated["id"]) }


@router.get('/mllp')
async def get_mllp_settings(current_user: Dict[str, Any] = Depends(require_super_admin())):
    """Get global MLLP listener configuration (super admin)."""
    try:
        cfg = await settings_service.get_system_setting('mllp_config', {})
        # Merge with env defaults for visibility
        merged = {
            'enabled': bool((cfg or {}).get('enabled') or (os.getenv('MLLP_ENABLED', 'false').lower() == 'true')),
            'host': (cfg or {}).get('host') or os.getenv('MLLP_HOST', '0.0.0.0'),
            'port': int((cfg or {}).get('port') or os.getenv('MLLP_PORT', '2575')),
            'ack_mode': (cfg or {}).get('ack_mode') or os.getenv('MLLP_ACK_MODE', 'auto'),
            'tenant_id': (cfg or {}).get('tenant_id') or os.getenv('MLLP_TENANT_ID'),
            'tenant_slug': (cfg or {}).get('tenant_slug') or os.getenv('MLLP_TENANT_SLUG'),
            'vendor_slug': (cfg or {}).get('vendor_slug') or os.getenv('MLLP_VENDOR_SLUG'),
            'tls_enabled': bool((cfg or {}).get('tls_enabled') or (os.getenv('MLLP_TLS_ENABLED', 'false').lower() == 'true')),
            'tls_cert_file': (cfg or {}).get('tls_cert_file') or os.getenv('MLLP_TLS_CERT'),
            'tls_key_file': (cfg or {}).get('tls_key_file') or os.getenv('MLLP_TLS_KEY'),
            'tls_ca_file': (cfg or {}).get('tls_ca_file') or os.getenv('MLLP_TLS_CA'),
            'require_client_cert': bool((cfg or {}).get('require_client_cert') or (os.getenv('MLLP_TLS_REQUIRE_CLIENT', 'false').lower() == 'true')),
        }
        return merged
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to get MLLP settings: {e}')


@router.patch('/mllp')
async def update_mllp_settings(payload: MLLPConfig, current_user: Dict[str, Any] = Depends(require_super_admin())):
    """Update global MLLP listener configuration (super admin)."""
    try:
        current = await settings_service.get_system_setting('mllp_config', {})
        if not isinstance(current, dict):
            current = {}
        merged = {**current, **{k: v for k, v in payload.model_dump(exclude_unset=True).items()}}
        await settings_service.set_system_setting('mllp_config', merged)
        return {'updated': True, 'mllp_config': merged}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to update MLLP settings: {e}')

@router.get("/platform-branding")
async def get_platform_branding(_: Dict[str, Any] = Depends(get_current_user)):
    return await settings_service.get_platform_branding()

@router.patch("/platform-branding")
async def update_platform_branding(payload: PlatformBranding, __: Dict[str, Any] = Depends(require_super_admin())):
    current = await settings_service.get_platform_branding()
    for f in ["app_name", "company_logo_url", "favicon_url", "idle_timeout_minutes", "idle_warning_seconds"]:
        v = getattr(payload, f)
        if v is not None:
            # enforce basic minimums for idle settings
            if f == "idle_timeout_minutes":
                try:
                    current[f] = max(1, int(v))
                except Exception:
                    continue
            elif f == "idle_warning_seconds":
                try:
                    current[f] = max(10, int(v))
                except Exception:
                    continue
            else:
                current[f] = v
    await settings_service.set_platform_branding(current)
    return current


class PlatformBilling(BaseModel):
    stripe_publishable_key: Optional[str] = None
    stripe_secret_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None

class PlatformEmail(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_tls: Optional[bool] = None

class Coupon(BaseModel):
    code: str
    percent_off: Optional[float] = None
    amount_off_cents: Optional[int] = None
    stripe_coupon_id: Optional[str] = None

@router.get("/platform-billing")
async def get_platform_billing(_: Dict[str, Any] = Depends(require_super_admin())):
    cfg = await settings_service.get_platform_config()
    stripe_cfg = cfg.get('stripe') or {}
    return {
        'stripe_publishable_key': stripe_cfg.get('publishable_key'),
        'stripe_secret_key': stripe_cfg.get('secret_key'),
        'stripe_webhook_secret': stripe_cfg.get('webhook_secret'),
        'configured': bool(stripe_cfg.get('secret_key')),
    }

@router.patch("/platform-billing")
async def update_platform_billing(payload: PlatformBilling, __: Dict[str, Any] = Depends(require_super_admin())):
    cfg = await settings_service.get_platform_config()
    stripe_cfg = dict(cfg.get('stripe') or {})
    if payload.stripe_publishable_key is not None:
        stripe_cfg['publishable_key'] = payload.stripe_publishable_key
    if payload.stripe_secret_key is not None:
        stripe_cfg['secret_key'] = payload.stripe_secret_key
    if payload.stripe_webhook_secret is not None:
        stripe_cfg['webhook_secret'] = payload.stripe_webhook_secret
    cfg['stripe'] = stripe_cfg
    await settings_service.set_platform_config(cfg)
    return {'updated': True, 'stripe': stripe_cfg}

@router.get("/platform-email")
async def get_platform_email(_: Dict[str, Any] = Depends(require_super_admin())):
    cfg = await settings_service.get_platform_config()
    email_cfg = cfg.get('email') or {}
    return {
        "smtp_host": email_cfg.get("smtp_host"),
        "smtp_port": email_cfg.get("smtp_port"),
        "smtp_user": email_cfg.get("smtp_user"),
        "smtp_from": email_cfg.get("smtp_from"),
        "smtp_tls": email_cfg.get("smtp_tls", True),
        # Never return password
        "has_password": bool(email_cfg.get("smtp_password")),
    }

@router.patch("/platform-email")
async def update_platform_email(payload: PlatformEmail, __: Dict[str, Any] = Depends(require_super_admin())):
    cfg = await settings_service.get_platform_config()
    email_cfg = dict(cfg.get('email') or {})
    if payload.smtp_host is not None:
        email_cfg['smtp_host'] = payload.smtp_host
    if payload.smtp_port is not None:
        email_cfg['smtp_port'] = payload.smtp_port
    if payload.smtp_user is not None:
        email_cfg['smtp_user'] = payload.smtp_user
    if payload.smtp_password is not None:
        email_cfg['smtp_password'] = payload.smtp_password
    if payload.smtp_from is not None:
        email_cfg['smtp_from'] = payload.smtp_from
    if payload.smtp_tls is not None:
        email_cfg['smtp_tls'] = payload.smtp_tls
    cfg['email'] = email_cfg
    await settings_service.set_platform_config(cfg)
    return {"updated": True, "email": {k: v for k, v in email_cfg.items() if k != "smtp_password"}}

class EmailTest(BaseModel):
    to: EmailStr
    subject: Optional[str] = "Test email from MedDataFlow"
    body: Optional[str] = "This is a test email from MedDataFlow SMTP settings."

@router.post("/platform-email/test")
async def test_platform_email(payload: EmailTest, __: Dict[str, Any] = Depends(require_super_admin())):
    from services.email_service import send_email
    ok = await send_email(payload.to, payload.subject or "Test email", payload.body or "Test")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to send test email. Check SMTP settings.")
    return {"sent": True}

@router.get("/platform-coupons")
async def list_platform_coupons(_: Dict[str, Any] = Depends(require_super_admin())):
    cfg = await settings_service.get_platform_config()
    return {"coupons": cfg.get("coupons", [])}

@router.patch("/platform-coupons")
async def update_platform_coupons(coupons: List[Coupon], __: Dict[str, Any] = Depends(require_super_admin())):
    cfg = await settings_service.get_platform_config()
    cfg["coupons"] = [c.model_dump() for c in coupons]
    await settings_service.set_platform_config(cfg)
    return {"updated": True, "coupons": cfg["coupons"]}

# ----------------------
# Logo upload endpoints
# ----------------------

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

def _safe_ext(filename: str) -> str:
    ext = ''.join(Path(filename).suffixes)
    if ext.lower() in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"]:
        return ext
    return ".png"

async def _restore_tenant_logo_file(tenant_id: uuid.UUID, settings: Dict[str, Any]) -> None:
    """If a tenant logo blob exists in settings, ensure the file is present on disk."""
    try:
        branding = settings.get("branding") or {}
        blob = branding.get("tenant_logo_blob")
        url = branding.get("tenant_logo_url")
        if not blob or not url or not str(url).startswith("/static/"):
            return
        rel = url.lstrip("/")
        out_path = STATIC_DIR / Path(rel).relative_to("static")
        if out_path.exists():
            return
        data_b64 = blob.get("base64_data")
        if not data_b64:
            return
        out_path.parent.mkdir(parents=True, exist_ok=True)
        content = base64.b64decode(data_b64)
        out_path.write_bytes(content)
    except Exception:
        # Best-effort restore; ignore failures
        return

@router.post("/tenant/logo")
async def upload_tenant_logo(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(require_tenant_admin()),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant),
    tenant_id: Optional[str] = Query(None)
):
    # Resolve target tenant id (super admin may override)
    target_tenant_id = current_tenant["id"]
    if tenant_id and current_user.get("role") == "SUPER_ADMIN":
        target_tenant_id = uuid.UUID(tenant_id)
    elif isinstance(target_tenant_id, str):
        target_tenant_id = uuid.UUID(target_tenant_id)

    # Persist file under static/uploads/tenants/{tenant_id}/
    ext = _safe_ext(file.filename or "")
    tenant_dir = STATIC_DIR / "uploads" / "tenants" / str(target_tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    fname = f"logo{ext}"
    out_path = tenant_dir / fname
    content = await file.read()
    url = f"/static/uploads/tenants/{target_tenant_id}/{fname}"
    try:
        out_path.write_bytes(content)
    except PermissionError:
        url = f"data:{file.content_type or 'image/png'};base64,{base64.b64encode(content).decode('ascii')}"

    # Update tenant settings.branding.tenant_logo_url
    tenant = await TenantRepository.get_tenant_by_id(target_tenant_id)
    current_settings = tenant.get("settings") or {}
    if isinstance(current_settings, str):
        try:
            current_settings = json.loads(current_settings)
        except Exception:
            current_settings = {}
    current_settings = dict(current_settings)
    branding = dict(current_settings.get("branding") or {})
    branding["tenant_logo_url"] = url
    branding["tenant_logo_blob"] = {
        "mime_type": file.content_type or "application/octet-stream",
        "base64_data": base64.b64encode(content).decode("ascii"),
    }
    current_settings["branding"] = branding
    await TenantRepository.update_tenant(target_tenant_id, settings=current_settings)
    return {"url": url}


@router.post("/platform-logo")
async def upload_platform_logo(
    file: UploadFile = File(...),
    __: Dict[str, Any] = Depends(require_super_admin()),
):
    ext = _safe_ext(file.filename or "")
    plat_dir = STATIC_DIR / "uploads" / "platform"
    plat_dir.mkdir(parents=True, exist_ok=True)
    fname = f"company-logo{ext}"
    out_path = plat_dir / fname
    content = await file.read()
    url = f"/static/uploads/platform/{fname}"
    try:
        out_path.write_bytes(content)
    except PermissionError:
        # Fallback to data URL if filesystem not writable
        url = f"data:{file.content_type or 'image/png'};base64,{base64.b64encode(content).decode('ascii')}"
    await settings_service.store_platform_asset("company_logo", url, content, file.content_type)
    return {"url": url}


@router.post("/platform-favicon")
async def upload_platform_favicon(
    file: UploadFile = File(...),
    __: Dict[str, Any] = Depends(require_super_admin()),
):
    ext = _safe_ext(file.filename or "")
    plat_dir = STATIC_DIR / "uploads" / "platform"
    plat_dir.mkdir(parents=True, exist_ok=True)
    fname = f"favicon{ext}"
    out_path = plat_dir / fname
    content = await file.read()
    url = f"/static/uploads/platform/{fname}"
    try:
        out_path.write_bytes(content)
    except PermissionError:
        url = f"data:{file.content_type or 'image/x-icon'};base64,{base64.b64encode(content).decode('ascii')}"
    await settings_service.store_platform_asset("favicon", url, content, file.content_type)
    return {"url": url}
