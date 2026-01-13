"""
Authentication router for meddataflow platform
"""
import uuid
from typing import Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
import os
import logging
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address

# Initialize limiter
limiter = Limiter(key_func=get_remote_address)

from services.auth_service import AuthService, TenantService
from services.email_service import send_email
from api.auth_deps import get_current_user, get_current_tenant, require_super_admin
from models.user import UserRole, UserRepository
from models.password_reset import PasswordResetRepository
from models.mfa_reset import MFAResetRepository

router = APIRouter(prefix="/api/auth", tags=["authentication"])
logger = logging.getLogger(__name__)


def _get_frontend_base_url() -> str:
    base = os.getenv('FRONTEND_URL') or os.getenv('PUBLIC_BASE_URL') or os.getenv('NEXT_PUBLIC_BASE_URL') or 'https://meddataflow.com'
    return base.rstrip('/')


def _build_reset_url(path: str, token: str) -> Optional[str]:
    base = _get_frontend_base_url()
    if not base:
        return None
    return f"{base}{path}?token={token}"

# Pydantic models
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_slug: Optional[str] = None
    totp_code: Optional[str] = None

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: Dict[str, Any]
    tenant: Dict[str, Any]

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    tenant_slug: str

class RegisterResponse(BaseModel):
    message: str
    user_id: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class UserProfile(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    is_active: bool
    is_verified: bool
    tenant_id: Optional[str]
    tenant_name: Optional[str]
    created_at: datetime
    last_login_at: Optional[datetime]
    impersonating: Optional[bool] = None
    original_role: Optional[str] = None
    original_tenant_id: Optional[str] = None

class SwitchTenantRequest(BaseModel):
    tenant_id: str

class SwitchTenantResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant: Dict[str, Any]
    user: Dict[str, Any]

class TwoFactorSetupResponse(BaseModel):
    secret: str
    qr_code: str
    backup_codes: list[str]
    provisioning_uri: str

class TwoFactorVerifyRequest(BaseModel):
    totp_code: str

class TwoFactorVerifyResponse(BaseModel):
    success: bool
    message: str

class TwoFactorDisableRequest(BaseModel):
    totp_code: str

class TwoFactorStatusResponse(BaseModel):
    enabled: bool

class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    tenant_slug: Optional[str] = None

class ForgotPasswordResponse(BaseModel):
    requested: bool

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
    confirm_password: Optional[str] = None
    
class ResetPasswordResponse(BaseModel):
    reset: bool
    message: str

class Forgot2FARequest(BaseModel):
    email: EmailStr
    tenant_slug: Optional[str] = None

class Forgot2FAResponse(BaseModel):
    requested: bool

class Reset2FARequest(BaseModel):
    token: str

class Reset2FAResponse(BaseModel):
    reset: bool
    message: str

# Authentication endpoints
@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def login(request: Request, login_request: LoginRequest):
    """User login endpoint"""
    user = await AuthService.authenticate_user(
        login_request.email, login_request.password, login_request.tenant_slug
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    # Enforce tenant activation for non-super-admins
    if user and user.get('role') != UserRole.SUPER_ADMIN:
        # Determine tenant to check
        tid = None
        if login_request.tenant_slug:
            tenant = await TenantService.get_tenant_by_slug(login_request.tenant_slug)
            if tenant:
                tid = tenant.get('id')
        elif user.get('tenant_id'):
            tid = user.get('tenant_id')
        if tid:
            from models.tenant import TenantRepository
            tid_uuid = tid if isinstance(tid, uuid.UUID) else uuid.UUID(str(tid))
            t = await TenantRepository.get_tenant_by_id(tid_uuid)
            if t and not t.get('is_active', False):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="TENANT_INACTIVE")

    # Check if 2FA is enabled
    if user.get('two_factor_enabled'):
        if not login_request.totp_code:
            raise HTTPException(
                status_code=status.HTTP_202_ACCEPTED,
                detail="2FA_REQUIRED"
            )

        # Verify 2FA code
        user_uuid = user['id'] if isinstance(user['id'], uuid.UUID) else uuid.UUID(user['id'])
        if not await UserRepository.verify_two_factor(user_uuid, login_request.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid 2FA code"
            )

    # Create tokens
    user_data = {
        'sub': str(user['id']),
        'email': user['email'],
        'role': user['role'],
        'tenant_id': str(user['tenant_id']) if user['tenant_id'] else None
    }

    access_token = AuthService.create_access_token(user_data)
    refresh_token = AuthService.create_refresh_token()

    # Create session (handle asyncpg UUID objects)
    user_uuid = user['id'] if isinstance(user['id'], uuid.UUID) else uuid.UUID(user['id'])
    await AuthService.create_user_session(
        user_uuid, access_token, refresh_token
    )

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=30 * 60,  # 30 minutes
        user={
            'id': str(user['id']),
            'email': user['email'],
            'first_name': user['first_name'],
            'last_name': user['last_name'],
            'role': user['role']
        },
        tenant={
            'id': str(user['tenant_id']) if user['tenant_id'] else None,
            'name': user.get('tenant_name'),
            'slug': user.get('tenant_slug')
        }
    )

@router.post("/register", response_model=RegisterResponse)
@limiter.limit("3/minute")
async def register(request: Request, register_request: RegisterRequest):
    """User registration endpoint"""
    user = await AuthService.register_user(
        email=register_request.email,
        password=register_request.password,
        first_name=register_request.first_name,
        last_name=register_request.last_name,
        tenant_slug=register_request.tenant_slug
    )
    
    return RegisterResponse(
        message="User registered successfully",
        user_id=str(user['id'])
    )

@router.post("/refresh")
async def refresh_token(request: RefreshTokenRequest):
    """Refresh access token"""
    token_data = await AuthService.refresh_access_token(request.refresh_token)
    
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    return token_data

@router.post("/logout")
async def logout(current_user: Dict[str, Any] = Depends(get_current_user)):
    """User logout endpoint"""
    # In a real implementation, you'd get the actual token from the request
    # For now, we'll just return success since session invalidation 
    # would happen on the frontend by removing the token
    return {"message": "Logged out successfully"}

@router.get("/me", response_model=UserProfile)
@router.get("/profile", response_model=UserProfile)
async def get_current_user_profile(
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Get current user profile"""
    return UserProfile(
        id=str(current_user['id']),
        email=current_user['email'],
        first_name=current_user['first_name'],
        last_name=current_user['last_name'],
        role=current_user['role'],
        is_active=current_user['is_active'],
        is_verified=current_user['is_verified'],
        tenant_id=str(current_user['tenant_id']) if current_user['tenant_id'] else None,
        tenant_name=current_user.get('tenant_name'),
        created_at=current_user['created_at'],
        last_login_at=current_user.get('last_login_at'),
        impersonating=current_user.get('impersonating'),
        original_role=current_user.get('original_role'),
        original_tenant_id=str(current_user.get('original_tenant_id')) if current_user.get('original_tenant_id') else None
    )

@router.get("/tenant")
async def get_current_tenant_info(
    tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get current tenant information"""
    return {
        'id': str(tenant['id']),
        'name': tenant['name'],
        'slug': tenant['slug'],
        'plan': tenant['plan'],
        'is_active': tenant['is_active'],
        'user_count': tenant.get('user_count', 0),
        'workflow_count': tenant.get('workflow_count', 0),
        'created_at': tenant['created_at'],
        'environment': ((tenant.get('settings') or {}).get('environment') if isinstance(tenant.get('settings'), dict) else None),
        'group_key': ((tenant.get('settings') or {}).get('group_key') if isinstance(tenant.get('settings'), dict) else None)
    }

@router.get("/my-tenants")
async def list_my_tenants(current_user: Dict[str, Any] = Depends(get_current_user)):
    """List tenants the current user can access (primary + memberships)."""
    import uuid as _uuid
    user_uuid = current_user['id'] if isinstance(current_user['id'], _uuid.UUID) else _uuid.UUID(str(current_user['id']))
    tenants = await TenantService.list_user_tenants(user_uuid)
    result = []
    for t in tenants:
        settings = t.get('settings') or {}
        result.append({
            'id': str(t['id']),
            'name': t['name'],
            'slug': t['slug'],
            'environment': (settings.get('environment') or '').upper() if isinstance(settings, dict) else None,
            'group_key': (settings.get('group_key') if isinstance(settings, dict) else None),
            'role': t.get('membership_role') or current_user.get('role')
        })
    return result

@router.post("/switch-tenant", response_model=SwitchTenantResponse)
async def switch_tenant(req: SwitchTenantRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Switch active tenant context by issuing a new token scoped to the target tenant."""
    import uuid as _uuid
    try:
        target_tid = _uuid.UUID(req.tenant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    # Verify membership: primary tenant or membership row
    my_tenants = await TenantService.list_user_tenants(current_user['id'] if isinstance(current_user['id'], _uuid.UUID) else _uuid.UUID(str(current_user['id'])))
    if not any(str(t['id']) == str(target_tid) for t in my_tenants):
        raise HTTPException(status_code=403, detail="Not a member of target tenant")

    # Mint a tenant-scoped token directly
    token = AuthService.create_access_token({
        'sub': str(current_user['id']),
        'email': current_user['email'],
        'role': current_user['role'],
        'tenant_id': str(target_tid),
    })

    return SwitchTenantResponse(
        access_token=token,
        expires_in=30 * 60,
        tenant={'id': str(target_tid)},
        user={
            'id': str(current_user['id']),
            'email': current_user['email'],
            'first_name': current_user['first_name'],
            'last_name': current_user['last_name'],
            'role': current_user['role']
        }
    )

# Admin endpoints
@router.post("/tenant")
async def create_tenant(
    name: str,
    slug: str,
    domain: Optional[str] = None,
    billing_email: Optional[str] = None,
    _: Dict[str, Any] = Depends(require_super_admin())
):
    """Create a new tenant (admin only)"""
    tenant = await TenantService.create_tenant(
        name=name,
        slug=slug,
        domain=domain,
        billing_email=billing_email
    )
    
    return {
        'id': str(tenant['id']),
        'name': tenant['name'],
        'slug': tenant['slug'],
        'api_key': tenant['api_key'],
        'created_at': tenant['created_at']
    }

@router.get("/validate-token")
async def validate_token(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Validate current token"""
    return {
        'valid': True,
        'user_id': str(current_user['id']),
        'email': current_user['email'],
        'role': current_user['role']
    }

# 2FA endpoints
@router.get("/2fa/status", response_model=TwoFactorStatusResponse)
async def get_2fa_status(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Get 2FA status for the current user"""
    return TwoFactorStatusResponse(enabled=current_user.get('two_factor_enabled', False))

@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Setup 2FA for the current user"""
    if current_user.get('two_factor_enabled'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled"
        )

    user_uuid = current_user['id'] if isinstance(current_user['id'], uuid.UUID) else uuid.UUID(current_user['id'])
    setup_data = await UserRepository.setup_two_factor(user_uuid)

    if not setup_data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to setup 2FA"
        )

    return TwoFactorSetupResponse(**setup_data)

@router.post("/2fa/enable", response_model=TwoFactorVerifyResponse)
async def enable_2fa(
    request: TwoFactorVerifyRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Enable 2FA after verifying the setup code"""
    if current_user.get('two_factor_enabled'):
        return TwoFactorVerifyResponse(success=False, message="2FA is already enabled")

    user_uuid = current_user['id'] if isinstance(current_user['id'], uuid.UUID) else uuid.UUID(current_user['id'])
    success = await UserRepository.enable_two_factor(user_uuid, request.totp_code)

    if success:
        return TwoFactorVerifyResponse(success=True, message="2FA enabled successfully")
    else:
        return TwoFactorVerifyResponse(success=False, message="Invalid code")

@router.post("/2fa/disable", response_model=TwoFactorVerifyResponse)
async def disable_2fa(
    request: TwoFactorDisableRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Disable 2FA after verifying a code"""
    if not current_user.get('two_factor_enabled'):
        return TwoFactorVerifyResponse(success=False, message="2FA is not enabled")

    user_uuid = current_user['id'] if isinstance(current_user['id'], uuid.UUID) else uuid.UUID(current_user['id'])

    # Verify the code before disabling
    if not await UserRepository.verify_two_factor(user_uuid, request.totp_code):
        return TwoFactorVerifyResponse(success=False, message="Invalid code")

    success = await UserRepository.disable_two_factor(user_uuid)

    if success:
        return TwoFactorVerifyResponse(success=True, message="2FA disabled successfully")
    else:
        return TwoFactorVerifyResponse(success=False, message="Failed to disable 2FA")

@router.post("/2fa/regenerate-backup-codes")
async def regenerate_backup_codes(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Regenerate backup codes for 2FA"""
    if not current_user.get('two_factor_enabled'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not enabled"
        )

    user_uuid = current_user['id'] if isinstance(current_user['id'], uuid.UUID) else uuid.UUID(current_user['id'])
    backup_codes = await UserRepository.regenerate_backup_codes(user_uuid)

    return {"backup_codes": backup_codes}


# Password reset endpoints
@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit("3/hour")
async def forgot_password(request: Request, req: ForgotPasswordRequest):
    """Request a password reset. Always return 200 to avoid user enumeration."""
    # Try to find user by email (global or by tenant_slug if provided)
    user = None
    if req.tenant_slug:
        tenant = await TenantService.get_tenant_by_slug(req.tenant_slug)
        if tenant:
            user = await UserRepository.get_user_by_email(req.email, tenant_id=tenant['id'])
    if not user:
        user = await UserRepository.get_user_by_email(req.email)

    try:
        if user and user.get('password_hash'):
            # Create reset request
            import uuid as _uuid
            uid = user['id'] if isinstance(user['id'], _uuid.UUID) else _uuid.UUID(str(user['id']))
            await PasswordResetRepository.invalidate_for_user(uid)
            pr = await PasswordResetRepository.create_request(uid, ttl_minutes=30)
            reset_link = _build_reset_url("/auth/reset-password", pr['token'])
            if reset_link:
                subject = "Reset your MedDataFlow password"
                body = (
                    "We received a request to reset your MedDataFlow password.\n\n"
                    f"Reset link:\n{reset_link}\n\n"
                    "This link expires in 30 minutes. If you did not request this, you can ignore this email."
                )
                html = (
                    "<p>We received a request to reset your MedDataFlow password.</p>"
                    f"<p><a href=\"{reset_link}\">Reset your password</a></p>"
                    "<p>This link expires in 30 minutes. If you did not request this, you can ignore this email.</p>"
                )
                await send_email(user.get('email') or req.email, subject, body, html)
            else:
                logger.warning("Password reset requested but FRONTEND_URL/PUBLIC_BASE_URL is not set; email not sent.")
    except Exception as exc:
        logger.warning(f"Failed to process password reset email: {exc}")
    return ForgotPasswordResponse(requested=True)

@router.post("/reset-password", response_model=ResetPasswordResponse)
@limiter.limit("5/minute")
async def reset_password(request: Request, req: ResetPasswordRequest):
    if req.confirm_password is not None and req.confirm_password != req.new_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    # Validate token
    pr = await PasswordResetRepository.get_valid_by_token(req.token)
    if not pr:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    # Update password
    import uuid as _uuid
    uid = pr['user_id'] if isinstance(pr['user_id'], _uuid.UUID) else _uuid.UUID(str(pr['user_id']))
    await UserRepository.update_password(uid, req.new_password)
    # Invalidate all active sessions for security
    try:
      from models.user import UserSessionRepository
      await UserSessionRepository.delete_all_for_user(uid)
    except Exception:
      pass
    # Invalidate token
    rid = pr['id'] if isinstance(pr['id'], _uuid.UUID) else _uuid.UUID(str(pr['id']))
    await PasswordResetRepository.mark_used(rid)
    return ResetPasswordResponse(reset=True, message="Password has been reset")


@router.post("/forgot-2fa", response_model=Forgot2FAResponse)
async def forgot_2fa(req: Forgot2FARequest):
    """Request 2FA reset (email-based). Always return 200 to prevent user enumeration."""
    user = None
    if req.tenant_slug:
        tenant = await TenantService.get_tenant_by_slug(req.tenant_slug)
        if tenant:
            user = await UserRepository.get_user_by_email(req.email, tenant_id=tenant['id'])
    if not user:
        user = await UserRepository.get_user_by_email(req.email)

    try:
        # Only allow if user exists, is verified, and has 2FA enabled
        if user and user.get('is_verified') and user.get('two_factor_enabled'):
            import uuid as _uuid
            uid = user['id'] if isinstance(user['id'], _uuid.UUID) else _uuid.UUID(str(user['id']))
            await MFAResetRepository.invalidate_for_user(uid)
            rec = await MFAResetRepository.create_request(uid, ttl_minutes=30)
            reset_link = _build_reset_url("/auth/reset-2fa", rec['token'])
            if reset_link:
                subject = "Reset your MedDataFlow two-factor authentication"
                body = (
                    "We received a request to reset two-factor authentication on your MedDataFlow account.\n\n"
                    f"Reset link:\n{reset_link}\n\n"
                    "This link expires in 30 minutes. If you did not request this, you can ignore this email."
                )
                html = (
                    "<p>We received a request to reset two-factor authentication on your MedDataFlow account.</p>"
                    f"<p><a href=\"{reset_link}\">Reset two-factor authentication</a></p>"
                    "<p>This link expires in 30 minutes. If you did not request this, you can ignore this email.</p>"
                )
                await send_email(user.get('email') or req.email, subject, body, html)
            else:
                logger.warning("2FA reset requested but FRONTEND_URL/PUBLIC_BASE_URL is not set; email not sent.")
    except Exception as exc:
        logger.warning(f"Failed to process 2FA reset email: {exc}")
    return Forgot2FAResponse(requested=True)


@router.post("/reset-2fa", response_model=Reset2FAResponse)
@limiter.limit("5/minute")
async def reset_2fa(request: Request, req: Reset2FARequest):
    pr = await MFAResetRepository.get_valid_by_token(req.token)
    if not pr:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    import uuid as _uuid
    uid = pr['user_id'] if isinstance(pr['user_id'], _uuid.UUID) else _uuid.UUID(str(pr['user_id']))
    # Disable 2FA
    success = await UserRepository.disable_two_factor(uid)
    rid = pr['id'] if isinstance(pr['id'], _uuid.UUID) else _uuid.UUID(str(pr['id']))
    await MFAResetRepository.mark_used(rid)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reset 2FA")
    return Reset2FAResponse(reset=True, message="Two-factor authentication has been disabled. You can re-enable it after login.")
