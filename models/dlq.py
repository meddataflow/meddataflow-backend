from typing import Optional, List, Dict, Any
import uuid
from datetime import datetime, timezone

from database.connection import fetch_one_dict, fetch_all_dict, execute_dict, fetch_one


class DLQRepository:
    @staticmethod
    async def add(
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
        execution_id: Optional[uuid.UUID],
        activity_id: Optional[uuid.UUID],
        activity_name: Optional[str],
        error_message: Optional[str],
        payload: Optional[Dict[str, Any]],
        max_retries: int = 0,
        next_attempt_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        return await fetch_one_dict(
            """
            INSERT INTO dlq_messages (tenant_id, workflow_id, execution_id, activity_id, activity_name,
                                      error_message, payload, retries, max_retries, next_attempt_at, status)
            VALUES (:tenant_id, :workflow_id, :execution_id, :activity_id, :activity_name,
                    :error_message, :payload, 0, :max_retries, :next_attempt_at, 'PENDING')
            RETURNING *
            """,
            {
                'tenant_id': str(tenant_id),
                'workflow_id': str(workflow_id),
                'execution_id': str(execution_id) if execution_id else None,
                'activity_id': str(activity_id) if activity_id else None,
                'activity_name': activity_name,
                'error_message': error_message,
                'payload': payload or {},
                'max_retries': max_retries,
                'next_attempt_at': next_attempt_at,
            }
        )

    @staticmethod
    async def list(
        tenant_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        where = ["tenant_id = :tenant_id"]
        params: Dict[str, Any] = {'tenant_id': str(tenant_id), 'limit': limit, 'offset': offset}
        if status:
            where.append("status = :status")
            params['status'] = status
        return await fetch_all_dict(
            f"SELECT * FROM dlq_messages WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT :limit OFFSET :offset",
            params
        )

    @staticmethod
    async def get(item_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        return await fetch_one_dict(
            "SELECT * FROM dlq_messages WHERE id = :id AND tenant_id = :tenant_id",
            {'id': str(item_id), 'tenant_id': str(tenant_id)}
        )

    @staticmethod
    async def mark(item_id: uuid.UUID, tenant_id: uuid.UUID, status: str, retries: Optional[int] = None) -> Optional[Dict[str, Any]]:
        sets = ["status = :status", "updated_at = NOW()"]
        params: Dict[str, Any] = {'id': str(item_id), 'tenant_id': str(tenant_id), 'status': status}
        if retries is not None:
            sets.append("retries = :retries")
            params['retries'] = retries
        return await fetch_one_dict(
            f"UPDATE dlq_messages SET {', '.join(sets)} WHERE id = :id AND tenant_id = :tenant_id RETURNING *",
            params
        )

    @staticmethod
    async def delete(item_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        await execute_dict("DELETE FROM dlq_messages WHERE id = :id AND tenant_id = :tenant_id", {'id': str(item_id), 'tenant_id': str(tenant_id)})
        return True

