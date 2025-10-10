from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Dict, Any, Optional
import uuid as _uuid

from api.auth_deps import get_current_user, get_current_tenant
from services.workflow_execution_service import workflow_execution_service

router = APIRouter(prefix="/api/fhir", tags=["fhir-subscriptions"])


@router.post("/subscriptions/notify")
async def fhir_notify(
    request: Request,
    workflow_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Receive FHIR subscription events and trigger a workflow.

    Body is forwarded as trigger variables under 'fhir_event'.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not workflow_id:
        # fallback to tenant setting: settings.integrations.fhir_subscription_workflow_id
        settings = current_tenant.get('settings') or {}
        if isinstance(settings, str):
            try:
                import json as _json
                settings = _json.loads(settings)
            except Exception:
                settings = {}
        workflow_id = ((settings.get('integrations') or {}).get('fhir_subscription_workflow_id'))
    if not workflow_id:
        raise HTTPException(status_code=400, detail='No workflow configured for FHIR subscriptions')

    result = await workflow_execution_service.execute_workflow(
        workflow_id=str(workflow_id),
        trigger_data={ 'source': 'fhir', 'fhir_event': data, 'fhir_resource_type': resource_type },
        tenant_id=str(current_tenant['id']),
        user_id=str(current_user['id'])
    )
    return { 'triggered': True, 'execution': result }

