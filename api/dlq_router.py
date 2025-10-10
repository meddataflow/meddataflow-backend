from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uuid as _uuid

from api.auth_deps import get_current_user, get_current_tenant
from models.dlq import DLQRepository
from services.workflow_execution_service import workflow_execution_service

router = APIRouter(prefix="/api/dlq", tags=["dlq"])


class DLQItem(BaseModel):
    id: str
    tenant_id: str
    workflow_id: str
    execution_id: Optional[str] = None
    activity_id: Optional[str] = None
    activity_name: Optional[str] = None
    error_message: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    retries: int
    max_retries: int
    next_attempt_at: Optional[str] = None
    status: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@router.get("/", response_model=List[DLQItem])
async def list_dlq(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    items = await DLQRepository.list(_uuid.UUID(str(current_tenant['id'])), status=status, limit=limit, offset=offset)
    out: List[DLQItem] = []
    for i in items:
        out.append(DLQItem(**{**i, 'id': str(i['id']), 'tenant_id': str(i['tenant_id']), 'workflow_id': str(i['workflow_id']),
                              'execution_id': str(i['execution_id']) if i.get('execution_id') else None,
                              'activity_id': str(i['activity_id']) if i.get('activity_id') else None}))
    return out


class RequeueRequest(BaseModel):
    mode: Optional[str] = 'workflow'  # workflow|drop
    trigger_data: Optional[Dict[str, Any]] = None


@router.post("/{item_id}/requeue")
async def requeue_item(
    item_id: str,
    req: RequeueRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    iid = _uuid.UUID(item_id)
    tid = _uuid.UUID(str(current_tenant['id']))
    item = await DLQRepository.get(iid, tid)
    if not item:
        raise HTTPException(status_code=404, detail='DLQ item not found')

    if (req.mode or 'workflow') == 'workflow':
        # re-run whole workflow with payload variables as trigger
        payload = (item.get('payload') or {})
        variables = (req.trigger_data or {})
        if isinstance(payload, dict):
            # only pass variables subset, avoid config echo
            vars_subset = payload.get('variables') or {}
            variables = {**vars_subset, **variables}
        result = await workflow_execution_service.execute_workflow(
            workflow_id=str(item['workflow_id']),
            trigger_data=variables,
            tenant_id=str(tid),
            user_id=str(current_user['id'])
        )
        await DLQRepository.mark(iid, tid, 'REQUEUED')
        return { 'requeued': True, 'execution': result }
    else:
        await DLQRepository.mark(iid, tid, 'RESOLVED')
        return { 'requeued': False, 'resolved': True }


@router.delete("/{item_id}")
async def delete_item(
    item_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    iid = _uuid.UUID(item_id)
    tid = _uuid.UUID(str(current_tenant['id']))
    await DLQRepository.delete(iid, tid)
    return { 'deleted': True }

