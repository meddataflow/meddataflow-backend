"""
Settings router: user and tenant settings management
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from pydantic import BaseModel, EmailStr
import uuid
from pathlib import Path
import json

from api.auth_deps import get_current_user, get_current_tenant, require_tenant_admin, require_super_admin
from models.user import UserRepository
from models.tenant import TenantRepository

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


# ----------------------
# Platform branding file
# ----------------------
BRANDING_PATH = Path(__file__).resolve().parent.parent / "config" / "branding.json"
PLATFORM_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "platform_config.json"

def _read_platform_branding() -> Dict[str, Any]:
    try:
        if BRANDING_PATH.exists():
            data = json.loads(BRANDING_PATH.read_text())
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    # defaults
    return {
        "app_name": "meddataflow",
        "company_logo_url": None,
        "favicon_url": None,
        "idle_timeout_minutes": 5,
        "idle_warning_seconds": 60,
    }

def _write_platform_branding(data: Dict[str, Any]) -> None:
    BRANDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    BRANDING_PATH.write_text(json.dumps(data, indent=2))

@router.get("/platform-branding")
async def get_platform_branding(_: Dict[str, Any] = Depends(get_current_user)):
    return _read_platform_branding()

@router.patch("/platform-branding")
async def update_platform_branding(payload: PlatformBranding, __: Dict[str, Any] = Depends(require_super_admin())):
    current = _read_platform_branding()
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
    _write_platform_branding(current)
    return current


class PlatformBilling(BaseModel):
    stripe_publishable_key: Optional[str] = None
    stripe_secret_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None

def _read_platform_config() -> Dict[str, Any]:
    try:
        if PLATFORM_CONFIG_PATH.exists():
            data = json.loads(PLATFORM_CONFIG_PATH.read_text())
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def _write_platform_config(data: Dict[str, Any]) -> None:
    PLATFORM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLATFORM_CONFIG_PATH.write_text(json.dumps(data, indent=2))

@router.get("/platform-billing")
async def get_platform_billing(_: Dict[str, Any] = Depends(require_super_admin())):
    cfg = _read_platform_config()
    stripe_cfg = cfg.get('stripe') or {}
    return {
        'stripe_publishable_key': stripe_cfg.get('publishable_key'),
        'stripe_secret_key': stripe_cfg.get('secret_key'),
        'stripe_webhook_secret': stripe_cfg.get('webhook_secret'),
        'configured': bool(stripe_cfg.get('secret_key')),
    }

@router.patch("/platform-billing")
async def update_platform_billing(payload: PlatformBilling, __: Dict[str, Any] = Depends(require_super_admin())):
    cfg = _read_platform_config()
    stripe_cfg = dict(cfg.get('stripe') or {})
    if payload.stripe_publishable_key is not None:
        stripe_cfg['publishable_key'] = payload.stripe_publishable_key
    if payload.stripe_secret_key is not None:
        stripe_cfg['secret_key'] = payload.stripe_secret_key
    if payload.stripe_webhook_secret is not None:
        stripe_cfg['webhook_secret'] = payload.stripe_webhook_secret
    cfg['stripe'] = stripe_cfg
    _write_platform_config(cfg)
    return {'updated': True, 'stripe': stripe_cfg}


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
    out_path.write_bytes(content)
    url = f"/static/uploads/tenants/{target_tenant_id}/{fname}"

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
    out_path.write_bytes(content)
    url = f"/static/uploads/platform/{fname}"
    data = _read_platform_branding()
    data["company_logo_url"] = url
    _write_platform_branding(data)
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
    out_path.write_bytes(content)
    url = f"/static/uploads/platform/{fname}"
    data = _read_platform_branding()
    data["favicon_url"] = url
    _write_platform_branding(data)
    return {"url": url}
