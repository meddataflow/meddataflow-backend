from typing import Optional, Dict, Any, List
import uuid
from datetime import datetime, timezone

from database.connection import fetch_one, fetch_all, execute_returning, execute


class BillingInvoiceRepository:
    @staticmethod
    async def create_invoice(
        tenant_id: uuid.UUID,
        provider: str,
        external_id: Optional[str],
        period_start: Optional[datetime],
        period_end: Optional[datetime],
        amount_cents: int,
        currency: str = 'USD',
        status: str = 'draft',
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        import json
        payload_json = payload
        if payload is None:
            payload_json = json.dumps({})
        else:
            try:
                payload_json = json.dumps(payload)
            except Exception:
                payload_json = json.dumps({})
        query = """
        INSERT INTO billing_invoices (tenant_id, provider, external_id, period_start, period_end, amount_cents, currency, status, payload, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10)
        RETURNING *
        """
        now = datetime.now(timezone.utc)
        return await execute_returning(query, tenant_id, provider, external_id, period_start, period_end, amount_cents, currency, status, payload_json, now)

    @staticmethod
    async def get_latest_invoice(tenant_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        query = """
        SELECT * FROM billing_invoices WHERE tenant_id = $1 ORDER BY period_end DESC NULLS LAST, created_at DESC LIMIT 1
        """
        return await fetch_one(query, tenant_id)

    @staticmethod
    async def list_invoices(tenant_id: uuid.UUID, limit: int = 24, offset: int = 0) -> List[Dict[str, Any]]:
        query = """
        SELECT * FROM billing_invoices WHERE tenant_id = $1 ORDER BY period_end DESC NULLS LAST, created_at DESC LIMIT $2 OFFSET $3
        """
        return await fetch_all(query, tenant_id, limit, offset)
