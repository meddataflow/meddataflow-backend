from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uuid as _uuid

from api.auth_deps import get_current_user, get_current_tenant
from models.quarantine import QuarantineRepository
from services.workflow_execution_service import workflow_execution_service

router = APIRouter(prefix="/api/hl7/quarantine", tags=["hl7-quarantine"])


class QuarantineItem(BaseModel):
    id: str
    tenant_id: str
    original_message_id: Optional[str] = None
    raw_message: str
    reason_code: str
    reason_detail: Optional[Dict[str, Any]] = None
    status: str
    target_workflow_id: Optional[str] = None
    created_at: Optional[str] = None
    replayed_at: Optional[str] = None


class CreateQuarantineRequest(BaseModel):
    raw_message: str
    reason_code: str
    reason_detail: Optional[Dict[str, Any]] = None
    original_message_id: Optional[str] = None
    target_workflow_id: Optional[str] = None


@router.get("/", response_model=List[QuarantineItem])
async def list_quarantine(
    limit: int = 50,
    offset: int = 0,
    status_filter: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    items = await QuarantineRepository.list_items(
        tenant_id=_uuid.UUID(str(current_tenant['id'])),
        limit=limit,
        offset=offset,
        status=status_filter
    )
    return [QuarantineItem(**{**i, 'id': str(i['id']), 'tenant_id': str(i['tenant_id']), 'original_message_id': str(i['original_message_id']) if i.get('original_message_id') else None, 'target_workflow_id': str(i['target_workflow_id']) if i.get('target_workflow_id') else None}) for i in items]


@router.post("/", response_model=QuarantineItem)
async def add_quarantine(
    req: CreateQuarantineRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    original_id = _uuid.UUID(req.original_message_id) if req.original_message_id else None
    target_wf = _uuid.UUID(req.target_workflow_id) if req.target_workflow_id else None
    item = await QuarantineRepository.add_item(
        tenant_id=_uuid.UUID(str(current_tenant['id'])),
        raw_message=req.raw_message,
        reason_code=req.reason_code,
        reason_detail=req.reason_detail,
        created_by_id=_uuid.UUID(str(current_user['id'])),
        original_message_id=original_id,
        target_workflow_id=target_wf,
    )
    return QuarantineItem(**{**item, 'id': str(item['id']), 'tenant_id': str(item['tenant_id']), 'original_message_id': str(item['original_message_id']) if item.get('original_message_id') else None, 'target_workflow_id': str(item['target_workflow_id']) if item.get('target_workflow_id') else None})


class ReplayRequest(BaseModel):
    workflow_id: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None


@router.post("/{item_id}/replay")
async def replay_quarantined(
    item_id: str,
    req: ReplayRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    iid = _uuid.UUID(item_id)
    tid = _uuid.UUID(str(current_tenant['id']))
    item = await QuarantineRepository.get_item(iid, tid)
    if not item:
        raise HTTPException(status_code=404, detail="Quarantine item not found")

    wf = req.workflow_id or (item.get('target_workflow_id') and str(item['target_workflow_id']))
    if not wf:
        raise HTTPException(status_code=400, detail="workflow_id is required to replay")

    # Execute workflow with the raw message
    result = await workflow_execution_service.execute_workflow(
        workflow_id=str(wf),
        trigger_data={'source': 'replay', 'message': item['raw_message'], **(req.variables or {})},
        tenant_id=str(tid),
        user_id=str(current_user['id'])
    )

    # Mark as replayed
    await QuarantineRepository.mark_replayed(iid, tid)
    return {
        'message': 'Replay triggered',
        'execution': result
    }


@router.delete("/{item_id}")
async def delete_quarantined(
    item_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    iid = _uuid.UUID(item_id)
    tid = _uuid.UUID(str(current_tenant['id']))
    await QuarantineRepository.delete_item(iid, tid)
    return {"deleted": True}

