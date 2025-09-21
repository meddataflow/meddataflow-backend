"""
Tenant user management router (tenant admin scope)
"""
import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

from api.auth_deps import get_current_user, require_tenant_admin
from models.user import UserRepository, UserRole
from database.connection import fetch_all, fetch_one, execute

router = APIRouter(prefix="/api/tenants", tags=["tenant-users"])


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[UserRole] = UserRole.VIEWER


class UpdateUserRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


def _assert_same_tenant_or_super(current_user: Dict[str, Any], tenant_id: uuid.UUID):
    # If impersonating, current_user['tenant_id'] may be overridden already
    user_role = current_user.get('role')
    if user_role == UserRole.SUPER_ADMIN:
        return
    cu_tid = current_user.get('tenant_id')
    cu_tid = cu_tid if isinstance(cu_tid, uuid.UUID) else uuid.UUID(str(cu_tid))
    if cu_tid != tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions for tenant")


@router.get("/{tenant_id}/users")
async def list_users(tenant_id: str, current_user: Dict[str, Any] = Depends(require_tenant_admin())):
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")
    _assert_same_tenant_or_super(current_user, tid)
    # Include primary users and membership users; membership role overrides
    query = """
    SELECT DISTINCT u.id, u.email, u.first_name, u.last_name, u.is_active, u.created_at, u.last_login_at,
           COALESCE(um.role::text, u.role::text) as effective_role
    FROM users u
    LEFT JOIN user_memberships um ON um.user_id = u.id AND um.tenant_id = $1
    WHERE (u.tenant_id = $1 OR um.user_id IS NOT NULL) AND u.is_active = true
    ORDER BY u.created_at DESC
    """
    users = await fetch_all(query, tid)
    return [
        {
            'id': str(u['id']),
            'email': u['email'],
            'first_name': u.get('first_name'),
            'last_name': u.get('last_name'),
            'role': u.get('effective_role'),
            'is_active': u.get('is_active', True),
            'created_at': u.get('created_at'),
            'last_login_at': u.get('last_login_at')
        }
        for u in users
    ]


@router.post("/{tenant_id}/users")
async def create_user(tenant_id: str, body: CreateUserRequest, current_user: Dict[str, Any] = Depends(require_tenant_admin())):
    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant id")
    _assert_same_tenant_or_super(current_user, tid)

    # Tenant admins cannot create super admins
    if body.role == UserRole.SUPER_ADMIN and current_user.get('role') != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot assign SUPER_ADMIN role")

    # If a user with this email exists globally, add membership; else create new user
    existing = await UserRepository.get_user_by_email(body.email)
    if existing:
        # Create membership if not exists
        try:
            await execute(
                """
                INSERT INTO user_memberships(user_id, tenant_id, role)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, tenant_id) DO UPDATE SET role = EXCLUDED.role
                """,
                existing['id'], tid, (body.role or UserRole.VIEWER).value
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to add membership: {e}")
        return {
            'id': str(existing['id']),
            'email': existing['email'],
            'role': (body.role or existing.get('role')).value if hasattr(body.role or existing.get('role'), 'value') else (body.role or existing.get('role')),
            'created_at': existing.get('created_at')
        }
    else:
        created = await UserRepository.create_user(
            email=body.email,
            password=body.password or "",
            first_name=body.first_name or "",
            last_name=body.last_name or "",
            tenant_id=tid,
            role=body.role or UserRole.VIEWER,
        )
        return {
            'id': str(created['id']),
            'email': created['email'],
            'role': created['role'],
            'created_at': created['created_at']
        }


@router.patch("/{tenant_id}/users/{user_id}")
async def update_user(tenant_id: str, user_id: str, body: UpdateUserRequest, current_user: Dict[str, Any] = Depends(require_tenant_admin())):
    try:
        tid = uuid.UUID(tenant_id)
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id")
    _assert_same_tenant_or_super(current_user, tid)

    # Tenant admins cannot set SUPER_ADMIN
    if body.role == UserRole.SUPER_ADMIN and current_user.get('role') != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot assign SUPER_ADMIN role")

    updates = body.dict(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    # If membership exists for this tenant, update membership role; else update user
    membership = await fetch_one("SELECT * FROM user_memberships WHERE user_id = $1 AND tenant_id = $2", uid, tid)
    if membership and 'role' in updates and updates['role']:
        await execute("UPDATE user_memberships SET role = $1 WHERE user_id = $2 AND tenant_id = $3", (updates['role'].value if hasattr(updates['role'], 'value') else updates['role']), uid, tid)
        # return basic info
        user = await UserRepository.get_user_by_id(uid)
        return {
            'id': str(uid),
            'email': user['email'] if user else '',
            'role': updates['role'].value if hasattr(updates['role'], 'value') else updates['role'],
            'is_active': user.get('is_active', True) if user else True,
            'updated_at': user.get('updated_at') if user else None
        }
    # Fallback to updating user core fields (first_name, last_name, role, is_active)
    updated = await UserRepository.update_user(uid, **{k: (v.value if hasattr(v, 'value') else v) for k, v in updates.items()})
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found or no changes")
    return {
        'id': str(updated['id']),
        'email': updated['email'],
        'role': updated['role'],
        'is_active': updated['is_active'],
        'updated_at': updated['updated_at']
    }


@router.delete("/{tenant_id}/users/{user_id}")
async def deactivate_user(tenant_id: str, user_id: str, current_user: Dict[str, Any] = Depends(require_tenant_admin())):
    try:
        tid = uuid.UUID(tenant_id)
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id")
    _assert_same_tenant_or_super(current_user, tid)
    # If user is primary of this tenant, deactivate user; else remove membership
    u = await UserRepository.get_user_by_id(uid)
    if not u:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    primary_tid = u.get('tenant_id')
    primary_tid = primary_tid if isinstance(primary_tid, uuid.UUID) else uuid.UUID(str(primary_tid)) if primary_tid else None
    if primary_tid == tid:
        updated = await UserRepository.update_user(uid, is_active=False)
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return {"id": str(updated['id']), "deactivated": True}
    else:
        await execute("DELETE FROM user_memberships WHERE user_id = $1 AND tenant_id = $2", uid, tid)
        return {"id": str(uid), "deactivated": True}
