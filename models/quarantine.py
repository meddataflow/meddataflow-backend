from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import uuid

from database.connection import fetch_one_dict, fetch_all_dict, execute_dict


class QuarantineRepository:
    @staticmethod
    async def add_item(
        tenant_id: uuid.UUID,
        raw_message: str,
        reason_code: str,
        reason_detail: Optional[Dict[str, Any]] = None,
        created_by_id: Optional[uuid.UUID] = None,
        original_message_id: Optional[uuid.UUID] = None,
        target_workflow_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        qid = str(uuid.uuid4())
        return await fetch_one_dict(
            """
            INSERT INTO message_quarantine (
                id, tenant_id, created_by_id, original_message_id,
                raw_message, reason_code, reason_detail, status, target_workflow_id
            ) VALUES (
                :id, :tenant_id, :created_by_id, :original_message_id,
                :raw_message, :reason_code, :reason_detail, 'PENDING', :target_workflow_id
            ) RETURNING *
            """,
            {
                'id': qid,
                'tenant_id': str(tenant_id),
                'created_by_id': str(created_by_id) if created_by_id else None,
                'original_message_id': str(original_message_id) if original_message_id else None,
                'raw_message': raw_message,
                'reason_code': reason_code,
                'reason_detail': reason_detail or {},
                'target_workflow_id': str(target_workflow_id) if target_workflow_id else None,
            }
        )

    @staticmethod
    async def list_items(
        tenant_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where = ["tenant_id = :tenant_id"]
        params: Dict[str, Any] = {'tenant_id': str(tenant_id), 'limit': limit, 'offset': offset}
        if status:
            where.append("status = :status")
            params['status'] = status
        return await fetch_all_dict(
            f"SELECT * FROM message_quarantine WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT :limit OFFSET :offset",
            params
        )

    @staticmethod
    async def get_item(item_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return await fetch_one_dict(
            "SELECT * FROM message_quarantine WHERE id = :id AND tenant_id = :tenant_id",
            {'id': str(item_id), 'tenant_id': str(tenant_id)}
        )

    @staticmethod
    async def delete_item(item_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        await execute_dict(
            "DELETE FROM message_quarantine WHERE id = :id AND tenant_id = :tenant_id",
            {'id': str(item_id), 'tenant_id': str(tenant_id)}
        )
        return True

    @staticmethod
    async def mark_replayed(item_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return await fetch_one_dict(
            """
            UPDATE message_quarantine
            SET status = 'REPLAYED', replayed_at = NOW()
            WHERE id = :id AND tenant_id = :tenant_id
            RETURNING *
            """,
            {'id': str(item_id), 'tenant_id': str(tenant_id)}
        )

