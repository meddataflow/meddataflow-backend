"""
Admin router for tenant management and impersonation
"""
import uuid
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth_deps import get_current_user, require_super_admin
from services.auth_service import AuthService
from models.tenant import TenantRepository
from models.user import UserRepository
from database.connection import fetch_all, execute
import json
import os
from database.connection import fetch_one

router = APIRouter(prefix="/api/admin/tenants", tags=["admin-tenants"])


class TenantCreateRequest(BaseModel):
    name: str
    slug: str
    domain: Optional[str] = None
    billing_email: Optional[str] = None
    billing_address: Optional[str] = None
    industry: Optional[str] = None
    team_size: Optional[str] = None
    primary_use_case: Optional[str] = None
    ehr_vendor: Optional[str] = None
    region: Optional[str] = None
    security_contact: Optional[str] = None
    onboarding_notes: Optional[str] = None


class TenantUpdateRequest(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    plan: Optional[str] = None
    is_active: Optional[bool] = None
    billing_email: Optional[str] = None
    billing_address: Optional[str] = None
    industry: Optional[str] = None
    team_size: Optional[str] = None
    primary_use_case: Optional[str] = None
    ehr_vendor: Optional[str] = None
    region: Optional[str] = None
    security_contact: Optional[str] = None
    onboarding_notes: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class ImpersonateResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    impersonating: bool = True
    tenant_id: str
    role: str = "TENANT_ADMIN"


@router.get("", dependencies=[Depends(require_super_admin())])
async def list_tenants():
    tenants = await TenantRepository.get_all_tenants_any_status()
    results = []
    for t in tenants:
        settings = t.get('settings') or {}
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except Exception:
                settings = {}
        results.append({
            'id': str(t["id"]),
            'name': t["name"],
            'slug': t["slug"],
            'domain': t.get("domain"),
            'plan': t.get("plan"),
            'is_active': t.get("is_active", True),
            'industry': t.get("industry"),
            'team_size': t.get("team_size"),
            'primary_use_case': t.get("primary_use_case"),
            'ehr_vendor': t.get("ehr_vendor"),
            'region': t.get("region"),
            'security_contact': t.get("security_contact"),
            'onboarding_notes': t.get("onboarding_notes"),
            'user_count': t.get('user_count', 0),
            'workflow_count': t.get('workflow_count', 0),
            'created_at': t.get('created_at'),
            'environment': (settings.get('environment') if isinstance(settings, dict) else None)
        })
    return results


@router.post("", dependencies=[Depends(require_super_admin())])
async def create_tenant(body: TenantCreateRequest):
    # validate slug is available is handled by service in existing code; reuse repository here
    if not await TenantRepository.validate_slug(body.slug):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant slug already exists")
    tenant = await TenantRepository.create_tenant(
        name=body.name,
        slug=body.slug,
        domain=body.domain,
        billing_email=body.billing_email,
        industry=body.industry,
        team_size=body.team_size,
        primary_use_case=body.primary_use_case,
        ehr_vendor=body.ehr_vendor,
        region=body.region,
        security_contact=body.security_contact,
        onboarding_notes=body.onboarding_notes,
    )
    return {
        'id': str(tenant['id']),
        'name': tenant['name'],
        'slug': tenant['slug'],
        'api_key': tenant['api_key'],
        'created_at': tenant['created_at']
    }


@router.patch("/{tenant_id}", dependencies=[Depends(require_super_admin())])
async def update_tenant(tenant_id: str, body: TenantUpdateRequest):
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")
    updates = {k: v for k, v in body.dict(exclude_unset=True).items()}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")
    updated = await TenantRepository.update_tenant(tid, **updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found or no changes")
    return {
        'id': str(updated['id']),
        'name': updated['name'],
        'slug': updated['slug'],
        'plan': updated.get('plan'),
        'is_active': updated.get('is_active', True),
        'updated_at': updated.get('updated_at')
    }


@router.delete("/{tenant_id}", dependencies=[Depends(require_super_admin())])
async def delete_tenant(tenant_id: str):
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")
    # Soft delete by deactivating
    updated = await TenantRepository.update_tenant(tid, is_active=False)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return {"id": str(updated['id']), "deleted": True}


@router.delete("/{tenant_id}/hard", dependencies=[Depends(require_super_admin())])
async def hard_delete_tenant(tenant_id: str):
    """Permanently delete a tenant and all related records.

    This leverages ON DELETE CASCADE for most tables. As a safety, it also
    removes user sessions for users belonging to the tenant before deletion.
    Returns 409 if the delete fails due to constraints in this deployment.
    """
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")

    # Ensure tenant exists (any status)
    tenant = await TenantRepository.get_tenant_by_id_any_status(tid)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    # Remove user sessions for users of this tenant to avoid FK issues in some setups
    try:
        await execute("DELETE FROM user_sessions WHERE user_id IN (SELECT id FROM users WHERE tenant_id = $1)", tid)
    except Exception:
        pass

    # Attempt to delete tenant (cascade should clean dependent rows)
    from database.connection import execute as _exec
    try:
        await _exec("DELETE FROM tenants WHERE id = $1", tid)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Failed to delete tenant: {e}")

    # Cleanup notifications
    try:
        from pathlib import Path
        import json as _json
        notif_path = Path(__file__).resolve().parent.parent / 'config' / 'notifications.json'
        if notif_path.exists():
            existing = _json.loads(notif_path.read_text()) or []
            if isinstance(existing, list):
                existing = [n for n in existing if str(n.get('tenant_id')) != tenant_id]
                notif_path.write_text(_json.dumps(existing, indent=2))
    except Exception:
        pass

    return {"id": tenant_id, "deleted": True}


@router.get("/{tenant_id}/stats", dependencies=[Depends(require_super_admin())])
async def tenant_stats(tenant_id: str):
    """Return related record counts for a tenant, regardless of active status."""
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")
    counts = {}
    for name, query in [
        ("users", "SELECT COUNT(*) AS c FROM users WHERE tenant_id = $1"),
        ("workflows", "SELECT COUNT(*) AS c FROM workflows WHERE tenant_id = $1"),
        ("messages", "SELECT COUNT(*) AS c FROM hl7_messages WHERE tenant_id = $1"),
        ("vendor_endpoints", "SELECT COUNT(*) AS c FROM vendor_endpoints WHERE tenant_id = $1"),
    ]:
        row = await fetch_one(query, tid)
        counts[name] = int(row.get('c', 0)) if row else 0
    return counts


@router.post("/{tenant_id}/impersonate", response_model=ImpersonateResponse)
async def impersonate_tenant_admin(tenant_id: str, current_user: Dict[str, Any] = Depends(require_super_admin())):
    """Issue an access token for super admin to operate as TENANT_ADMIN of a tenant."""
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")

    # Ensure tenant exists and active
    tenant = await TenantRepository.get_tenant_by_id(tid)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    claims = {
        'sub': str(current_user['id']),
        'email': current_user['email'],
        'role': current_user['role'],  # original role preserved
        'tenant_id': str(current_user['tenant_id']) if current_user.get('tenant_id') else None,
        'impersonate_tenant_id': str(tid),
        'impersonate_role': 'TENANT_ADMIN',
    }
    access_token = AuthService.create_access_token(claims)
    return ImpersonateResponse(
        access_token=access_token,
        expires_in=30 * 60,
        tenant_id=str(tid),
    )


@router.post("/{tenant_id}/enable-staging")
async def enable_staging(tenant_id: str, current_user: Dict[str, Any] = Depends(require_super_admin())):
    """Provision a staging tenant for the given tenant and grant memberships to existing users."""
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")

    src = await TenantRepository.get_tenant_by_id(tid)
    if not src:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    base_name = src['name']
    base_slug = src['slug']
    stage_name = f"{base_name} (Staging)"
    stage_slug = f"{base_slug}-stage"
    # Ensure slug availability; if taken, append random suffix
    if not await TenantRepository.validate_slug(stage_slug):
        stage_slug = f"{stage_slug}-{str(uuid.uuid4())[:6]}"

    stage = await TenantRepository.create_tenant(
        name=stage_name,
        slug=stage_slug,
        domain=None,
        billing_email=src.get('billing_email')
    )

    # Helper to coerce settings into a dict
    def _as_dict(val):
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    # Update settings to mark environment and group
    settings = _as_dict(stage.get('settings'))
    src_settings = _as_dict(src.get('settings'))
    settings['environment'] = 'STAGE'
    settings['group_key'] = src_settings.get('group_key') or base_slug
    # Mark billing exempt by default for staging
    billing = settings.get('billing') or {}
    billing['billing_exempt'] = True
    settings['billing'] = billing

    await TenantRepository.update_tenant(stage['id'], settings=settings)

    # Grant memberships to all users of source tenant
    users = await UserRepository.get_users_by_tenant(tid)
    for u in users:
        try:
            await execute(
                """
                INSERT INTO user_memberships(user_id, tenant_id, role)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, tenant_id) DO NOTHING
                """,
                u['id'], stage['id'], u['role']
            )
        except Exception:
            pass

    return {
        'id': str(stage['id']),
        'name': stage_name,
        'slug': stage_slug,
        'environment': 'STAGE'
    }


@router.post("/{tenant_id}/approve", dependencies=[Depends(require_super_admin())])
async def approve_tenant(tenant_id: str):
    """Approve a tenant awaiting admin activation: set tenant active, reactivate users, and set subscription active."""
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")

    # Use get_tenant_by_id_any_status to get inactive tenants awaiting approval
    tenant = await TenantRepository.get_tenant_by_id_any_status(tid)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    # Normalize settings to dict
    settings = tenant.get('settings') or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    billing = dict(settings.get('billing') or {})
    billing['subscription_status'] = 'active'
    settings['billing'] = billing

    # Activate tenant
    await TenantRepository.update_tenant(tid, is_active=True, settings=settings)
    # Reactivate all users for this tenant
    try:
        await execute("UPDATE users SET is_active = TRUE WHERE tenant_id = $1", tid)
    except Exception:
        pass

    # Remove notification entry for this tenant
    from pathlib import Path
    notif_path = Path(__file__).resolve().parent.parent / 'config' / 'notifications.json'
    try:
        import json as _json
        existing = []
        if notif_path.exists():
            existing = _json.loads(notif_path.read_text()) or []
        if isinstance(existing, list):
            # Remove any notifications for this tenant
            existing = [n for n in existing if str(n.get('tenant_id')) != tenant_id]
            notif_path.write_text(_json.dumps(existing, indent=2))
    except Exception:
        pass

    # Send notifications
    try:
        from services.email_service import send_email
        from models.user import UserRepository
        import logging
        logger = logging.getLogger(__name__)

        supers = await UserRepository.list_super_admin_emails()
        subj_admin = f"Tenant approved: {tenant.get('name')}"
        base_url = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
        manage_link = f"{base_url}/admin/tenants" if base_url else None
        body_admin = f"Tenant '{tenant.get('name')}' (slug: {tenant.get('slug')}) has been approved and activated."
        if manage_link:
            body_admin += f"\n{manage_link}"
        html_admin = None
        if manage_link:
            html_admin = f"<p>Tenant '<strong>{tenant.get('name')}</strong>' (slug: <code>{tenant.get('slug')}</code>) has been approved and activated.</p><p><a href=\"{manage_link}\">Manage tenants</a></p>"

        # Send to super admins
        for addr in supers:
            logger.info(f"Sending approval notification to super admin: {addr}")
            await send_email(addr, subj_admin, body_admin, html_admin)

        # Send to tenant admin users
        tenant_admins_query = """
        SELECT email FROM users
        WHERE tenant_id = $1 AND role = 'TENANT_ADMIN' AND is_active = TRUE
        """
        tenant_admins = await fetch_all(tenant_admins_query, tid)

        for admin_row in tenant_admins:
            admin_email = admin_row.get('email')
            if admin_email:
                logger.info(f"Sending activation email to tenant admin: {admin_email}")
                await send_email(
                    admin_email,
                    "Your MedDataFlow tenant is active",
                    f"Good news! Your tenant '{tenant.get('name')}' has been approved and is now active. You may log in and begin using the platform.",
                    f"<p>Good news! Your tenant '<strong>{tenant.get('name')}</strong>' has been approved and is now active.</p><p>You may now <a href=\"{base_url}/auth/login\">log in</a> and begin using the platform.</p>"
                )

        # Also send to billing email if different
        if tenant.get('billing_email') and tenant.get('billing_email') not in [a.get('email') for a in tenant_admins]:
            logger.info(f"Sending activation email to billing contact: {tenant.get('billing_email')}")
            await send_email(
                tenant['billing_email'],
                "Your MedDataFlow tenant is active",
                f"Your tenant '{tenant.get('name')}' has been approved and is now active.",
                f"<p>Your tenant '<strong>{tenant.get('name')}</strong>' has been approved and is now active.</p>"
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error sending approval notifications: {e}")
        pass

    return {
        'id': tenant_id,
        'approved': True
    }
