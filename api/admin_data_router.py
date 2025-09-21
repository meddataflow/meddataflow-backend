"""
Super admin data access across all tenants
"""
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr

from api.auth_deps import require_super_admin
from models.user import UserRepository, UserSessionRepository
from models.tenant import TenantRepository
from database.connection import fetch_all, fetch_one, execute
try:
    from asyncpg import UndefinedTableError
except Exception:  # pragma: no cover
    class UndefinedTableError(Exception):
        pass

router = APIRouter(prefix="/api/admin", tags=["admin-data"])


@router.get("/users", dependencies=[Depends(require_super_admin())])
async def list_all_users(
    tenant_id: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conditions = ["u.is_active = true"]
    params = []
    param = 1
    if tenant_id:
        conditions.append(f"u.tenant_id = ${param}")
        params.append(uuid.UUID(tenant_id))
        param += 1
    if role:
        conditions.append(f"u.role = ${param}")
        params.append(role)
        param += 1
    if search:
        conditions.append(f"(u.email ILIKE ${param} OR u.first_name ILIKE ${param} OR u.last_name ILIKE ${param})")
        params.append(f"%{search}%")
        param += 1
    where_clause = " AND ".join(conditions) if conditions else "true"
    query = f"""
    SELECT u.*, t.name as tenant_name, t.slug as tenant_slug
    FROM users u
    LEFT JOIN tenants t ON u.tenant_id = t.id
    WHERE {where_clause}
    ORDER BY u.created_at DESC
    LIMIT ${param} OFFSET ${param + 1}
    """
    params.extend([limit, offset])
    rows = await fetch_all(query, *params)
    # normalize UUIDs to strings where necessary
    def norm(row):
        row = dict(row)
        for k in ("id", "tenant_id"):
            if k in row and row[k] is not None:
                row[k] = str(row[k])
        return row
    return [norm(r) for r in rows]


@router.get("/notifications", dependencies=[Depends(require_super_admin())])
async def list_notifications():
    from pathlib import Path
    import json as _json
    notif_path = Path(__file__).resolve().parent.parent / 'config' / 'notifications.json'
    if notif_path.exists():
        try:
            data = _json.loads(notif_path.read_text())
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


@router.get("/pending-approvals", dependencies=[Depends(require_super_admin())])
async def list_pending_approvals():
    """List tenants whose billing subscription_status is 'pending_approval'."""
    import json as _json
    from pathlib import Path
    notif_index: dict[str, str] = {}
    notif_path = Path(__file__).resolve().parent.parent / 'config' / 'notifications.json'
    if notif_path.exists():
        try:
            data = _json.loads(notif_path.read_text())
            if isinstance(data, list):
                for n in data:
                    if n.get('type') == 'TENANT_PENDING_APPROVAL' and n.get('tenant_id'):
                        notif_index[str(n['tenant_id'])] = n.get('timestamp')
        except Exception:
            pass

    tenants = await TenantRepository.get_all_tenants_any_status()
    results = []
    for t in tenants:
        settings = t.get('settings') or {}
        if isinstance(settings, str):
            try:
                settings = _json.loads(settings)
            except Exception:
                settings = {}
        billing = settings.get('billing') or {}
        if billing.get('subscription_status') == 'pending_approval':
            results.append({
                'id': str(t['id']),
                'name': t.get('name'),
                'slug': t.get('slug'),
                'billing_email': t.get('billing_email'),
                'requested_at': notif_index.get(str(t['id'])) or (t.get('updated_at') or t.get('created_at')),
                'is_active': t.get('is_active', False),
            })
    # Sort by requested_at desc
    results.sort(key=lambda x: (x.get('requested_at') or ''), reverse=True)
    return results


@router.post("/approvals/{tenant_id}/reject", dependencies=[Depends(require_super_admin())])
async def reject_pending_tenant(tenant_id: str):
    """Reject a pending tenant request: set subscription_status to 'rejected' and keep inactive.
    Also removes related notifications entries.
    """
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant id")
    # Update billing status
    import json as _json
    tenant = await TenantRepository.get_tenant_by_id_any_status(tid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    settings = tenant.get('settings') or {}
    if isinstance(settings, str):
        try:
            settings = _json.loads(settings)
        except Exception:
            settings = {}
    billing = dict(settings.get('billing') or {})
    billing['subscription_status'] = 'rejected'
    settings['billing'] = billing
    await TenantRepository.update_tenant(tid, is_active=False, settings=settings)
    # Remove notifications for this tenant
    from pathlib import Path
    notif_path = Path(__file__).resolve().parent.parent / 'config' / 'notifications.json'
    try:
        existing = []
        if notif_path.exists():
            existing = _json.loads(notif_path.read_text()) or []
        if isinstance(existing, list):
            existing = [n for n in existing if str(n.get('tenant_id')) != tenant_id]
            notif_path.write_text(_json.dumps(existing, indent=2))
    except Exception:
        pass
    return {'id': tenant_id, 'rejected': True}


@router.post("/users/{user_id}/disable-2fa", dependencies=[Depends(require_super_admin())])
async def admin_disable_user_2fa(user_id: str):
    """Disable 2FA for a user (Super Admin only).

    Sets two_factor_enabled = false and clears the 2FA secret. Also revokes all active sessions
    to require re-authentication. Returns basic user info.
    """
    try:
        uid = uuid.UUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    # Attempt to disable 2FA (idempotent)
    await UserRepository.disable_two_factor(uid)
    # Revoke sessions for safety
    try:
        await UserSessionRepository.delete_all_for_user(uid)
    except Exception:
        pass

    user = await UserRepository.get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Normalize for response
    result = {
        'id': str(user.get('id')),
        'email': user.get('email'),
        'two_factor_enabled': bool(user.get('two_factor_enabled', False)),
        'tenant_id': str(user['tenant_id']) if user.get('tenant_id') else None,
    }
    return {'updated': True, 'user': result}


class AdminUpdateUserRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    role: str | None = None
    is_active: bool | None = None


@router.patch("/users/{user_id}", dependencies=[Depends(require_super_admin())])
async def admin_update_user(user_id: str, body: AdminUpdateUserRequest):
    try:
        uid = uuid.UUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    updates: dict[str, object] = {}
    if body.first_name is not None:
        updates['first_name'] = body.first_name
    if body.last_name is not None:
        updates['last_name'] = body.last_name
    if body.email is not None:
        updates['email'] = body.email
    if body.role is not None:
        updates['role'] = body.role
    if body.is_active is not None:
        updates['is_active'] = body.is_active

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    updated = await UserRepository.update_user(uid, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    # Normalize
    result = {
        'id': str(updated.get('id')),
        'email': updated.get('email'),
        'first_name': updated.get('first_name'),
        'last_name': updated.get('last_name'),
        'role': updated.get('role'),
        'is_active': updated.get('is_active'),
    }
    return {'updated': True, 'user': result}


@router.delete("/users/{user_id}", dependencies=[Depends(require_super_admin())])
async def admin_delete_user(user_id: str):
    """Soft-delete a user by setting is_active = false."""
    try:
        uid = uuid.UUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    updated = await UserRepository.update_user(uid, is_active=False)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        await UserSessionRepository.delete_all_for_user(uid)
    except Exception:
        pass
    return {'id': user_id, 'deactivated': True}


@router.delete("/users/{user_id}/hard", dependencies=[Depends(require_super_admin())])
async def admin_hard_delete_user(user_id: str, fallback_user_id: Optional[str] = None):
    """Permanently delete a user if there are no blocking references.

    Returns 409 if the user still has related records (e.g., messages, workflows).
    """
    try:
        uid = uuid.UUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    # Check references that may block deletion or need reassignment
    blockers = []
    counts = {}
    table_checks = [
        ("user_sessions", "SELECT COUNT(*) AS c FROM user_sessions WHERE user_id = $1"),
        ("user_memberships", "SELECT COUNT(*) AS c FROM user_memberships WHERE user_id = $1"),
        ("hl7_messages", "SELECT COUNT(*) AS c FROM hl7_messages WHERE created_by_id = $1"),
        ("workflows", "SELECT COUNT(*) AS c FROM workflows WHERE created_by_id = $1"),
        ("transformations", "SELECT COUNT(*) AS c FROM transformations WHERE created_by_id = $1"),
        ("data_tables", "SELECT COUNT(*) AS c FROM data_tables WHERE created_by_id = $1"),
    ]
    for name, query in table_checks:
        try:
            row = await fetch_one(query, uid)
            counts[name] = int(row.get('c', 0)) if row else 0
        except UndefinedTableError:
            # Table not present in this deployment; treat as zero references
            counts[name] = 0
        except Exception as e:
            # If the table truly doesn't exist or other minor metadata issues, continue as zero
            if 'undefined table' in str(e).lower() or 'relation' in str(e).lower():
                counts[name] = 0
            else:
                raise
        if counts[name] > 0 and name in ("hl7_messages", "workflows", "transformations", "data_tables"):
            blockers.append(f"{name}:{counts[name]}")

    # If there are creator references, require a fallback user to reassign
    if blockers and not fallback_user_id:
        raise HTTPException(status_code=409, detail=f"Cannot delete user; existing related records ({', '.join(blockers)}). Provide fallback_user_id to reassign.")

    # Reassign created_by_id to fallback user if provided
    if fallback_user_id:
        try:
            fid = uuid.UUID(fallback_user_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid fallback_user_id")
        # Ensure fallback user exists and is active
        fu = await fetch_one("SELECT id, is_active FROM users WHERE id = $1", fid)
        if not fu or fu.get('is_active') is False:
            raise HTTPException(status_code=404, detail="Fallback user not found or inactive")
        # Perform reassignment
        for query in [
            "UPDATE workflows SET created_by_id = $2 WHERE created_by_id = $1",
            "UPDATE transformations SET created_by_id = $2 WHERE created_by_id = $1",
            "UPDATE data_tables SET created_by_id = $2 WHERE created_by_id = $1",
            "UPDATE hl7_messages SET created_by_id = $2 WHERE created_by_id = $1",
        ]:
            await execute(query, uid, fid)

    # Clean up safe relations
    await execute("DELETE FROM user_sessions WHERE user_id = $1", uid)
    await execute("DELETE FROM user_memberships WHERE user_id = $1", uid)

    # Attempt delete
    try:
        await execute("DELETE FROM users WHERE id = $1", uid)
    except Exception as e:
        raise HTTPException(status_code=409, detail=f"Failed to delete user: {e}")

    return {"id": user_id, "deleted": True}


class MembershipBody(BaseModel):
    tenant_id: str
    role: str = 'VIEWER'


@router.get("/users/{user_id}/memberships", dependencies=[Depends(require_super_admin())])
async def admin_list_user_memberships(user_id: str):
    try:
        uid = uuid.UUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    q = """
    SELECT um.user_id, um.tenant_id, um.role, t.name as tenant_name, t.slug as tenant_slug
    FROM user_memberships um
    JOIN tenants t ON t.id = um.tenant_id
    WHERE um.user_id = $1
    ORDER BY t.name
    """
    rows = await fetch_all(q, uid)
    return [{
        'tenant_id': str(r['tenant_id']),
        'tenant_name': r['tenant_name'],
        'tenant_slug': r['tenant_slug'],
        'role': r['role']
    } for r in rows]


@router.post("/users/{user_id}/memberships", dependencies=[Depends(require_super_admin())])
async def admin_add_user_membership(user_id: str, body: MembershipBody):
    try:
        uid = uuid.UUID(user_id)
        tid = uuid.UUID(body.tenant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    tenant = await TenantRepository.get_tenant_by_id(tid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    try:
        existing = await fetch_one("SELECT 1 FROM user_memberships WHERE user_id = $1 AND tenant_id = $2", uid, tid)
        if existing:
            await execute("UPDATE user_memberships SET role = $1 WHERE user_id = $2 AND tenant_id = $3", body.role, uid, tid)
        else:
            await execute("INSERT INTO user_memberships(user_id, tenant_id, role) VALUES ($1, $2, $3)", uid, tid, body.role)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to save membership: {e}")
    return {'user_id': user_id, 'tenant_id': body.tenant_id, 'role': body.role}


@router.patch("/users/{user_id}/memberships/{tenant_id}", dependencies=[Depends(require_super_admin())])
async def admin_update_user_membership(user_id: str, tenant_id: str, body: MembershipBody):
    try:
        uid = uuid.UUID(user_id)
        tid = uuid.UUID(tenant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    await execute("UPDATE user_memberships SET role = $1 WHERE user_id = $2 AND tenant_id = $3", body.role, uid, tid)
    return {'user_id': user_id, 'tenant_id': tenant_id, 'role': body.role}


@router.delete("/users/{user_id}/memberships/{tenant_id}", dependencies=[Depends(require_super_admin())])
async def admin_delete_user_membership(user_id: str, tenant_id: str):
    try:
        uid = uuid.UUID(user_id)
        tid = uuid.UUID(tenant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    await execute("DELETE FROM user_memberships WHERE user_id = $1 AND tenant_id = $2", uid, tid)
    return {'user_id': user_id, 'tenant_id': tenant_id, 'deleted': True}


@router.get("/workflows", dependencies=[Depends(require_super_admin())])
async def list_all_workflows(
    tenant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conditions = ["true"]
    params = []
    param = 1
    if tenant_id:
        conditions.append(f"w.tenant_id = ${param}")
        params.append(uuid.UUID(tenant_id))
        param += 1
    if status:
        conditions.append(f"w.status = ${param}")
        params.append(status)
        param += 1
    where_clause = " AND ".join(conditions)
    query = f"""
    SELECT w.*, t.name as tenant_name, t.slug as tenant_slug,
           u.email as created_by_email
    FROM workflows w
    LEFT JOIN tenants t ON w.tenant_id = t.id
    LEFT JOIN users u ON w.created_by_id = u.id
    WHERE {where_clause}
    ORDER BY w.created_at DESC
    LIMIT ${param} OFFSET ${param + 1}
    """
    params.extend([limit, offset])
    rows = await fetch_all(query, *params)
    def norm(r):
        r = dict(r)
        for k in ("id", "tenant_id", "created_by_id"):
            if k in r and r[k] is not None:
                r[k] = str(r[k])
        return r
    return [norm(r) for r in rows]


@router.get("/vendor-endpoints", dependencies=[Depends(require_super_admin())])
async def list_all_vendor_endpoints(
    tenant_id: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conditions = ["true"]
    params = []
    param = 1
    if tenant_id:
        conditions.append(f"ve.tenant_id = ${param}")
        params.append(uuid.UUID(tenant_id))
        param += 1
    if is_active is not None:
        conditions.append(f"ve.is_active = ${param}")
        params.append(is_active)
        param += 1
    where_clause = " AND ".join(conditions)
    query = f"""
    SELECT ve.*, t.name as tenant_name, t.slug as tenant_slug,
           COALESCE(stats.total_messages, 0) as total_messages
    FROM vendor_endpoints ve
    LEFT JOIN tenants t ON ve.tenant_id = t.id
    LEFT JOIN (
        SELECT vendor_endpoint_id, COUNT(*) as total_messages
        FROM hl7_messages
        GROUP BY vendor_endpoint_id
    ) stats ON stats.vendor_endpoint_id = ve.id
    WHERE {where_clause}
    ORDER BY ve.created_at DESC
    LIMIT ${param} OFFSET ${param + 1}
    """
    params.extend([limit, offset])
    rows = await fetch_all(query, *params)
    def norm(r):
        r = dict(r)
        for k in ("id", "tenant_id"):
            if k in r and r[k] is not None:
                r[k] = str(r[k])
        return r
    return [norm(r) for r in rows]


@router.get("/hl7-messages", dependencies=[Depends(require_super_admin())])
async def list_all_messages(
    tenant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    message_type: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conditions = ["true"]
    params = []
    param = 1
    if tenant_id:
        conditions.append(f"m.tenant_id = ${param}")
        params.append(uuid.UUID(tenant_id))
        param += 1
    if status:
        conditions.append(f"m.status = ${param}")
        params.append(status)
        param += 1
    if message_type:
        conditions.append(f"m.message_type = ${param}")
        params.append(message_type)
        param += 1
    if start_date:
        conditions.append(f"m.created_at >= ${param}")
        params.append(start_date)
        param += 1
    if end_date:
        conditions.append(f"m.created_at <= ${param}")
        params.append(end_date)
        param += 1
    where_clause = " AND ".join(conditions)
    query = f"""
    SELECT m.*, t.name as tenant_name, t.slug as tenant_slug,
           u.email as created_by_email,
           w.name as workflow_name,
           ve.vendor_name
    FROM hl7_messages m
    LEFT JOIN tenants t ON m.tenant_id = t.id
    LEFT JOIN users u ON m.created_by_id = u.id
    LEFT JOIN workflows w ON m.workflow_id = w.id
    LEFT JOIN vendor_endpoints ve ON m.vendor_endpoint_id = ve.id
    WHERE {where_clause}
    ORDER BY m.created_at DESC
    LIMIT ${param} OFFSET ${param + 1}
    """
    params.extend([limit, offset])
    rows = await fetch_all(query, *params)
    def norm(r):
        r = dict(r)
        for k in ("id", "tenant_id", "workflow_id", "vendor_endpoint_id", "created_by_id"):
            if k in r and r[k] is not None:
                r[k] = str(r[k])
        return r
    return [norm(r) for r in rows]
