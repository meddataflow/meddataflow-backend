from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import uuid
import hashlib
import secrets

from database.connection import fetch_one, execute_returning, execute


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class PasswordResetRepository:
    @staticmethod
    async def create_request(user_id: uuid.UUID, ttl_minutes: int = 30, *, user_agent: Optional[str] = None, ip_address: Optional[str] = None) -> Dict[str, Any]:
        token_plain = secrets.token_urlsafe(32)
        token_hash = _hash_token(token_plain)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        query = """
        INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at, created_at, user_agent, ip_address)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, user_id, token_hash, expires_at, created_at
        """
        rid = uuid.uuid4()
        rec = await execute_returning(query, rid, user_id, token_hash, expires_at, datetime.now(timezone.utc), user_agent, ip_address)
        rec['token'] = token_plain
        return rec

    @staticmethod
    async def invalidate_for_user(user_id: uuid.UUID) -> None:
        query = """
        UPDATE password_reset_tokens
        SET used_at = NOW()
        WHERE user_id = $1
          AND used_at IS NULL
        """
        await execute(query, user_id)

    @staticmethod
    async def get_valid_by_token(token_plain: str) -> Optional[Dict[str, Any]]:
        token_hash = _hash_token(token_plain)
        query = """
        SELECT prt.*, u.email, u.tenant_id
        FROM password_reset_tokens prt
        JOIN users u ON prt.user_id = u.id
        WHERE prt.token_hash = $1
          AND prt.expires_at > NOW()
          AND prt.used_at IS NULL
        LIMIT 1
        """
        return await fetch_one(query, token_hash)

    @staticmethod
    async def mark_used(request_id: uuid.UUID) -> None:
        query = "UPDATE password_reset_tokens SET used_at = NOW() WHERE id = $1"
        await execute(query, request_id)

