"""
Authentication service for meddataflow platform
"""
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import uuid
import hashlib
import secrets
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from models.user import UserRepository, UserSessionRepository, UserRole
from models.tenant import TenantRepository

# JWT Configuration
import os
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("JWT_SECRET_KEY environment variable is required for security")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# Security
security = HTTPBearer()

class AuthService:
    @staticmethod
    async def authenticate_user(email: str, password: str, tenant_slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Authenticate user with email and password"""
        # Get tenant if slug provided
        tenant = None
        if tenant_slug:
            tenant = await TenantRepository.get_tenant_by_slug(tenant_slug)
            if not tenant:
                return None
        
        # Get user (handle asyncpg UUID objects)
        tenant_id = tenant['id'] if tenant and isinstance(tenant['id'], uuid.UUID) else uuid.UUID(tenant['id']) if tenant else None
        user = await UserRepository.get_user_by_email(email, tenant_id)
        
        if not user or not user.get('password_hash'):
            return None
        
        # Verify password
        if not await UserRepository.verify_password(password, user['password_hash']):
            return None
        
        # Update last login (handle asyncpg UUID objects)
        user_uuid = user['id'] if isinstance(user['id'], uuid.UUID) else uuid.UUID(user['id'])
        await UserRepository.update_last_login(user_uuid)
        
        return user
    
    @staticmethod
    def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT access token"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def create_refresh_token() -> str:
        """Create secure refresh token"""
        return secrets.token_urlsafe(32)
    
    @staticmethod
    def hash_token(token: str) -> str:
        """Hash token for database storage"""
        return hashlib.sha256(token.encode()).hexdigest()
    
    @staticmethod
    async def create_user_session(
        user_id: uuid.UUID,
        access_token: str,
        refresh_token: str,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create user session"""
        # Store refresh token hash for refresh endpoint lookup
        token_hash = AuthService.hash_token(refresh_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        
        return await UserSessionRepository.create_session(
            user_id, token_hash, expires_at, user_agent, ip_address
        )
    
    @staticmethod
    async def verify_token(token: str) -> Optional[Dict[str, Any]]:
        """Verify JWT token and return payload"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload
        except ExpiredSignatureError:
            return None
        except InvalidTokenError:
            return None
    
    @staticmethod
    async def get_current_user_from_token(token: str) -> Optional[Dict[str, Any]]:
        """Get current user from JWT token"""
        payload = await AuthService.verify_token(token)
        if not payload:
            return None
        
        user_id = payload.get('sub')
        if not user_id:
            return None
        
        try:
            # Handle asyncpg UUID objects
            user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
            user = await UserRepository.get_user_by_id(user_uuid)

            # Support super-admin impersonation context via token claims
            imp_tenant = payload.get('impersonate_tenant_id')
            imp_role = payload.get('impersonate_role')
            if imp_tenant and imp_role and user and user.get('role') == UserRole.SUPER_ADMIN:
                # Preserve original context while overriding effective access
                user = dict(user)
                user['original_role'] = user.get('role')
                user['original_tenant_id'] = user.get('tenant_id')
                user['impersonating'] = True
                # Override effective context
                try:
                    user['tenant_id'] = imp_tenant if isinstance(imp_tenant, uuid.UUID) else uuid.UUID(imp_tenant)
                except (ValueError, TypeError):
                    # If tenant id in token is malformed, ignore impersonation
                    pass
                user['role'] = imp_role

            # Apply tenant override from token claim (for tenant switching)
            token_tenant = payload.get('tenant_id')
            if token_tenant:
                try:
                    user['tenant_id'] = uuid.UUID(str(token_tenant))
                except Exception:
                    pass
            return user
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    async def refresh_access_token(refresh_token: str) -> Optional[Dict[str, str]]:
        """Refresh access token using refresh token"""
        token_hash = AuthService.hash_token(refresh_token)
        session = await UserSessionRepository.get_session_by_token(token_hash)
        
        if not session:
            return None
        
        # Create new access token
        user_data = {
            'sub': str(session['user_id']),
            'email': session['email'],
            'role': session['role'],
            'tenant_id': str(session['tenant_id']) if session['tenant_id'] else None
        }
        
        access_token = AuthService.create_access_token(user_data)
        
        # Update session last used
        await UserSessionRepository.update_session_last_used(token_hash)
        
        return {
            'access_token': access_token,
            'token_type': 'bearer',
            'expires_in': ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }
    
    @staticmethod
    async def logout_user(token: str) -> bool:
        """Logout user by invalidating session"""
        token_hash = AuthService.hash_token(token)
        await UserSessionRepository.delete_session(token_hash)
        return True
    
    @staticmethod
    async def register_user(
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        tenant_slug: str,
        role: UserRole = UserRole.VIEWER
    ) -> Dict[str, Any]:
        """Register a new user"""
        # Get tenant
        tenant = await TenantRepository.get_tenant_by_slug(tenant_slug)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found"
            )
        
        # Check if user already exists
        tenant_uuid = tenant['id'] if isinstance(tenant['id'], uuid.UUID) else uuid.UUID(tenant['id'])
        existing_user = await UserRepository.get_user_by_email(email, tenant_uuid)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )
        
        # Create user
        user = await UserRepository.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            tenant_id=tenant_uuid,
            role=role
        )
        
        return user
    
    @staticmethod
    def check_permission(user_role: str, required_roles: list) -> bool:
        """Check if user has required permissions"""
        role_hierarchy = {
            UserRole.SUPER_ADMIN: 5,
            UserRole.TENANT_ADMIN: 4,
            UserRole.WORKFLOW_ADMIN: 3,
            UserRole.ANALYST: 2,
            UserRole.VIEWER: 1
        }
        
        user_level = role_hierarchy.get(user_role, 0)
        required_level = min([role_hierarchy.get(role, 0) for role in required_roles])
        
        return user_level >= required_level

class TenantService:
    @staticmethod
    async def create_tenant(
        name: str,
        slug: str,
        domain: Optional[str] = None,
        billing_email: Optional[str] = None,
        **metadata: Any
    ) -> Dict[str, Any]:
        """Create a new tenant"""
        # Validate slug availability
        if not await TenantRepository.validate_slug(slug):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant slug already exists"
            )
        
        return await TenantRepository.create_tenant(
            name=name,
            slug=slug,
            domain=domain,
            billing_email=billing_email,
            **metadata
        )
    
    @staticmethod
    async def get_tenant_by_api_key(api_key: str) -> Optional[Dict[str, Any]]:
        """Get tenant by API key"""
        return await TenantRepository.get_tenant_by_api_key(api_key)
    
    @staticmethod
    async def verify_tenant_access(tenant_id: uuid.UUID, user_tenant_id: uuid.UUID) -> bool:
        """Verify user has access to tenant"""
        # Primary tenant access
        if tenant_id == user_tenant_id:
            return True
        # Membership-based access
        try:
            # Cheap check using repository
            memberships = await TenantRepository.get_tenants_for_user(user_tenant_id)
            return any(str(t['id']) == str(tenant_id) for t in memberships)
        except Exception:
            return False

    @staticmethod
    async def list_user_tenants(user_id: uuid.UUID) -> list[dict]:
        return await TenantRepository.get_tenants_for_user(user_id)

    @staticmethod
    def mint_tenant_scoped_token(user: Dict[str, Any], tenant_id: uuid.UUID) -> str:
        data = {
            'sub': str(user['id']),
            'email': user['email'],
            'role': user['role'],
            'tenant_id': str(tenant_id)
        }
        return AuthService.create_access_token(data)
