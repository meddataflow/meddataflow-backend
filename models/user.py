"""
User model and repository for meddataflow platform
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid
import secrets
import json
import pyotp
import qrcode
from io import BytesIO
import base64
from database.connection import fetch_one, fetch_all, execute_returning, execute
from passlib.context import CryptContext

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    TENANT_ADMIN = "TENANT_ADMIN"
    WORKFLOW_ADMIN = "WORKFLOW_ADMIN"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"

class AuthProvider(str, Enum):
    LOCAL = "LOCAL"
    AUTH0 = "AUTH0"
    SAML = "SAML"
    GOOGLE = "GOOGLE"
    MICROSOFT = "MICROSOFT"

class UserRepository:
    @staticmethod
    async def create_user(
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        tenant_id: uuid.UUID,
        role: UserRole = UserRole.VIEWER,
        auth_provider: AuthProvider = AuthProvider.LOCAL
    ) -> Dict[str, Any]:
        """Create a new user"""
        user_id = uuid.uuid4()
        password_hash = pwd_context.hash(password) if password else None
        
        query = """
        INSERT INTO users (
            id, tenant_id, email, first_name, last_name, password_hash,
            auth_provider, role, is_active, is_verified, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11)
        RETURNING *
        """
        
        now = datetime.now(timezone.utc)
        return await execute_returning(
            query, user_id, tenant_id, email, first_name, last_name,
            password_hash, auth_provider.value, role.value, True, False, now
        )
    
    @staticmethod
    async def get_user_by_id(user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        query = """
        SELECT u.*, t.name as tenant_name, t.slug as tenant_slug
        FROM users u
        LEFT JOIN tenants t ON u.tenant_id = t.id
        WHERE u.id = $1 AND u.is_active = true
        """
        return await fetch_one(query, user_id)
    
    @staticmethod
    async def get_user_by_email(email: str, tenant_id: Optional[uuid.UUID] = None) -> Optional[Dict[str, Any]]:
        """Get user by email"""
        if tenant_id:
            query = """
            SELECT u.*, t.name as tenant_name, t.slug as tenant_slug
            FROM users u
            LEFT JOIN tenants t ON u.tenant_id = t.id
            WHERE u.email = $1 AND u.tenant_id = $2 AND u.is_active = true
            """
            return await fetch_one(query, email, tenant_id)
        else:
            query = """
            SELECT u.*, t.name as tenant_name, t.slug as tenant_slug
            FROM users u
            LEFT JOIN tenants t ON u.tenant_id = t.id
            WHERE u.email = $1 AND u.is_active = true
            """
            return await fetch_one(query, email)
    
    @staticmethod
    async def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify password"""
        return pwd_context.verify(plain_password, hashed_password)
    
    @staticmethod
    async def update_last_login(user_id: uuid.UUID) -> None:
        """Update user last login timestamp"""
        query = """
        UPDATE users 
        SET last_login_at = $1, login_count = login_count + 1
        WHERE id = $2
        """
        await execute(query, datetime.now(timezone.utc), user_id)
    
    @staticmethod
    async def get_users_by_tenant(tenant_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Get all users for a tenant"""
        query = """
        SELECT u.*, t.name as tenant_name, t.slug as tenant_slug
        FROM users u
        LEFT JOIN tenants t ON u.tenant_id = t.id
        WHERE u.tenant_id = $1 AND u.is_active = true
        ORDER BY u.created_at DESC
        """
        return await fetch_all(query, tenant_id)
    
    @staticmethod
    async def update_user(user_id: uuid.UUID, **updates) -> Optional[Dict[str, Any]]:
        """Update user fields"""
        if not updates:
            return None
            
        # Build dynamic update query
        set_clauses = []
        values = []
        param_count = 1
        
        for field, value in updates.items():
            if field in ['first_name', 'last_name', 'role', 'permissions', 'is_active', 'timezone', 'preferences']:
                set_clauses.append(f"{field} = ${param_count}")
                values.append(value)
                param_count += 1
        
        if not set_clauses:
            return None
        
        set_clauses.append(f"updated_at = ${param_count}")
        values.append(datetime.now(timezone.utc))
        param_count += 1
        
        values.append(user_id)  # for WHERE clause
        
        query = f"""
        UPDATE users 
        SET {', '.join(set_clauses)}
        WHERE id = ${param_count}
        RETURNING *
        """
        
        return await execute_returning(query, *values)

    @staticmethod
    async def update_password(user_id: uuid.UUID, new_password: str) -> Optional[Dict[str, Any]]:
        """Update user's password hash"""
        password_hash = pwd_context.hash(new_password) if new_password else None
        if not password_hash:
            return None
        query = """
        UPDATE users
        SET password_hash = $1, updated_at = $2
        WHERE id = $3
        RETURNING *
        """
        return await execute_returning(query, password_hash, datetime.now(timezone.utc), user_id)

    @staticmethod
    async def setup_two_factor(user_id: uuid.UUID) -> Dict[str, Any]:
        """Setup two-factor authentication for a user"""
        secret = pyotp.random_base32()
        backup_codes = [secrets.token_hex(8) for _ in range(10)]

        query = """
        UPDATE users
        SET two_factor_secret = $1, backup_codes = $2, updated_at = $3
        WHERE id = $4
        RETURNING email, first_name, last_name
        """
        user = await execute_returning(
            query, secret, json.dumps(backup_codes), datetime.now(timezone.utc), user_id
        )

        if user:
            totp = pyotp.TOTP(secret)
            # Generate QR code for Google Authenticator
            email = user['email']
            issuer = "meddataflow"
            provisioning_uri = totp.provisioning_uri(
                name=email,
                issuer_name=issuer
            )

            # Create QR code
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(provisioning_uri)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()

            return {
                'secret': secret,
                'qr_code': f"data:image/png;base64,{qr_code_base64}",
                'backup_codes': backup_codes,
                'provisioning_uri': provisioning_uri
            }
        return None

    @staticmethod
    async def enable_two_factor(user_id: uuid.UUID, totp_code: str) -> bool:
        """Enable two-factor authentication after verifying the setup"""
        # Get the user's secret
        user = await UserRepository.get_user_by_id(user_id)
        if not user or not user.get('two_factor_secret'):
            return False

        # Verify the TOTP code
        totp = pyotp.TOTP(user['two_factor_secret'])
        if not totp.verify(totp_code):
            return False

        # Enable 2FA
        query = """
        UPDATE users
        SET two_factor_enabled = true, updated_at = $1
        WHERE id = $2
        """
        await execute(query, datetime.now(timezone.utc), user_id)
        return True

    @staticmethod
    async def disable_two_factor(user_id: uuid.UUID) -> bool:
        """Disable two-factor authentication"""
        query = """
        UPDATE users
        SET two_factor_enabled = false, two_factor_secret = NULL, backup_codes = '[]', updated_at = $1
        WHERE id = $2
        """
        await execute(query, datetime.now(timezone.utc), user_id)
        return True

    @staticmethod
    async def verify_two_factor(user_id: uuid.UUID, code: str) -> bool:
        """Verify a two-factor authentication code"""
        user = await UserRepository.get_user_by_id(user_id)
        if not user or not user.get('two_factor_enabled') or not user.get('two_factor_secret'):
            return False

        # First try TOTP verification
        totp = pyotp.TOTP(user['two_factor_secret'])
        if totp.verify(code):
            return True

        # If TOTP fails, check backup codes
        backup_codes = user.get('backup_codes', [])
        if code in backup_codes:
            # Remove the used backup code
            backup_codes.remove(code)
            query = """
            UPDATE users
            SET backup_codes = $1, updated_at = $2
            WHERE id = $3
            """
            await execute(query, json.dumps(backup_codes), datetime.now(timezone.utc), user_id)
            return True

        return False

    @staticmethod
    async def regenerate_backup_codes(user_id: uuid.UUID) -> List[str]:
        """Regenerate backup codes for a user"""
        backup_codes = [secrets.token_hex(8) for _ in range(10)]

        query = """
        UPDATE users
        SET backup_codes = $1, updated_at = $2
        WHERE id = $3
        """
        await execute(query, json.dumps(backup_codes), datetime.now(timezone.utc), user_id)
        return backup_codes

    @staticmethod
    async def list_super_admin_emails() -> List[str]:
        query = "SELECT email FROM users WHERE role = 'SUPER_ADMIN' AND is_active = true"
        rows = await fetch_all(query)
        return [r['email'] for r in rows if r.get('email')]

class UserSessionRepository:
    @staticmethod
    async def create_session(
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new user session"""
        session_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        
        query = """
        INSERT INTO user_sessions (
            id, user_id, token_hash, expires_at, created_at, last_used_at, user_agent, ip_address
        ) VALUES ($1, $2, $3, $4, $5, $5, $6, $7)
        RETURNING *
        """
        
        return await execute_returning(
            query, session_id, user_id, token_hash, expires_at, now, user_agent, ip_address
        )
    
    @staticmethod
    async def get_session_by_token(token_hash: str) -> Optional[Dict[str, Any]]:
        """Get session by token hash"""
        query = """
        SELECT s.*, u.email, u.first_name, u.last_name, u.role, u.tenant_id
        FROM user_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.token_hash = $1 AND s.expires_at > $2
        """
        return await fetch_one(query, token_hash, datetime.now(timezone.utc))
    
    @staticmethod
    async def update_session_last_used(token_hash: str) -> None:
        """Update session last used timestamp"""
        query = """
        UPDATE user_sessions 
        SET last_used_at = $1
        WHERE token_hash = $2
        """
        await execute(query, datetime.now(timezone.utc), token_hash)
    
    @staticmethod
    async def delete_session(token_hash: str) -> None:
        """Delete a session"""
        query = "DELETE FROM user_sessions WHERE token_hash = $1"
        await execute(query, token_hash)
    
    @staticmethod
    async def delete_expired_sessions() -> None:
        """Delete expired sessions"""
        query = "DELETE FROM user_sessions WHERE expires_at < $1"
        await execute(query, datetime.now(timezone.utc))

    @staticmethod
    async def delete_all_for_user(user_id: uuid.UUID) -> None:
        """Delete all sessions for a specific user"""
        query = "DELETE FROM user_sessions WHERE user_id = $1"
        await execute(query, user_id)
