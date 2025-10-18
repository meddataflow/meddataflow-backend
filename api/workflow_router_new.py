from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid
import json
import logging

from database.connection import fetch_one_dict, fetch_all_dict, execute_dict, fetch_one
from services.s3_service import s3_service
from models.pydantic_models import Workflow, WorkflowCreate, WorkflowUpdate, WorkflowActivity, WorkflowActivityCreate, WorkflowActivityUpdate
from api.auth_deps import get_current_user
from models.tenant import TenantRepository

router = APIRouter(prefix="/api/workflows", tags=["Workflows"])
logger = logging.getLogger(__name__)

def validate_uuid(uuid_string: str) -> uuid.UUID:
    try:
        return uuid.UUID(uuid_string)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")


def _normalize_trigger_payload(trigger_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize trigger/test data to support generic payloads and message formats."""
    data = dict(trigger_data or {})

    payload = data.pop("payload", None)
    if payload is not None:
        data.setdefault("raw_message", payload)
        data.setdefault("message", payload)

    message_format = data.get("message_format") or data.get("format")
    if message_format:
        data["message_format"] = message_format
        data.setdefault("format", message_format)

        format_aliases = {
            "hl7": ["hl7_payload"],
            "fhir": ["fhir_payload"],
            "dicom": ["dicom_payload", "dicom_file"],
            "ncpdp": ["ncpdp_payload"],
            "x12": ["x12_payload"],
            "cda": ["cda_payload"],
            "ccd": ["ccd_payload"],
            "ccr": ["ccr_payload"],
            "terminology": ["terminology_payload"],
        }
        aliases = format_aliases.get(str(message_format).lower())
        if aliases and payload is not None:
            for alias in aliases:
                data.setdefault(alias, payload)

    return data

@router.post("/", response_model=Workflow)
async def create_workflow(workflow_data: WorkflowCreate, current_user: Dict[str, Any] = Depends(get_current_user)):
    import uuid
    
    def serialize_workflow(workflow: dict) -> dict:
        # Convert UUID fields to strings
        for key in ["id", "tenant_id", "created_by_id"]:
            if key in workflow and isinstance(workflow[key], uuid.UUID):
                workflow[key] = str(workflow[key])
        # Ensure dict fields are valid dictionaries
        for key in ["settings", "environment_variables"]:
            if key in workflow and not isinstance(workflow[key], dict):
                workflow[key] = {}
        return workflow
        
    workflow_id = str(uuid.uuid4())
    query = """INSERT INTO workflows (id, tenant_id, created_by_id, name, description) 
               VALUES (:id, :tenant_id, :created_by_id, :name, :description) RETURNING *"""
    values = {
        "id": workflow_id,
        "tenant_id": current_user["tenant_id"],
        "created_by_id": current_user["id"],
        "name": workflow_data.name,
        "description": workflow_data.description
    }
    new_workflow = await fetch_one_dict(query, values)
    return serialize_workflow(new_workflow)

@router.get("/", response_model=List[Workflow])
async def list_workflows(current_user: Dict[str, Any] = Depends(get_current_user)):
    import uuid

    def serialize_workflow(workflow: dict) -> dict:
        # Convert UUID fields to strings
        for key in ["id", "tenant_id", "created_by_id"]:
            if key in workflow and isinstance(workflow[key], uuid.UUID):
                workflow[key] = str(workflow[key])
        # Ensure dict fields are valid dictionaries
        for key in ["settings", "environment_variables"]:
            if key in workflow and not isinstance(workflow[key], dict):
                workflow[key] = {}
        return workflow

    query = "SELECT * FROM workflows WHERE tenant_id = :tenant_id"
    values = {"tenant_id": current_user["tenant_id"]}
    workflows = await fetch_all_dict(query, values)
    workflows = [serialize_workflow(wf) for wf in workflows]
    return workflows

@router.get("/{workflow_id}", response_model=Workflow)
async def get_workflow(workflow_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    import uuid

    def serialize_workflow(workflow: dict) -> dict:
        # Convert UUID fields to strings
        for key in ["id", "tenant_id", "created_by_id"]:
            if key in workflow and isinstance(workflow[key], uuid.UUID):
                workflow[key] = str(workflow[key])
        # Ensure dict fields are valid dictionaries
        for key in ["settings", "environment_variables"]:
            if key in workflow and not isinstance(workflow[key], dict):
                workflow[key] = {}
        return workflow

    workflow_uuid = validate_uuid(workflow_id)
    query = "SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id"
    values = {"id": str(workflow_uuid), "tenant_id": current_user["tenant_id"]}
    workflow = await fetch_one_dict(query, values)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    workflow = serialize_workflow(workflow)
    return workflow

@router.put("/{workflow_id}", response_model=Workflow)
async def update_workflow(workflow_id: str, workflow_data: WorkflowUpdate, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Partially update a workflow. Only provided fields are modified."""
    import json as _json

    def serialize_workflow(workflow: dict) -> dict:
        # Convert UUID fields to strings
        for key in ["id", "tenant_id", "created_by_id"]:
            if key in workflow and isinstance(workflow[key], uuid.UUID):
                workflow[key] = str(workflow[key])
        # Ensure dict fields are valid dictionaries
        for key in ["settings", "environment_variables"]:
            if key in workflow and isinstance(workflow[key], str):
                try:
                    workflow[key] = _json.loads(workflow[key])
                except Exception:
                    workflow[key] = {}
        return workflow

    workflow_uuid = validate_uuid(workflow_id)

    # Determine fields to update from payload
    data = workflow_data.model_dump(exclude_unset=True)
    allowed_fields = {
        'name', 'description', 'version', 'status', 'execution_mode',
        'settings', 'environment_variables', 'max_concurrent_executions',
        'timeout_seconds', 'retry_attempts', 'cron_expression', 'trigger_endpoint_id'
    }
    set_clauses = []
    values: Dict[str, Any] = {
        'id': str(workflow_uuid),
        'tenant_id': current_user['tenant_id'],
    }

    for field, value in data.items():
        if field not in allowed_fields:
            continue
        # JSON-serialize dict fields for broad DB compatibility
        if field in ('settings', 'environment_variables') and value is not None:
            value = _json.dumps(value)
        set_clauses.append(f"{field} = :{field}")
        values[field] = value

    # Always update timestamp
    set_clauses.append("updated_at = now()")

    if len(set_clauses) == 1:
        # No updatable fields provided; fetch current row
        existing = await fetch_one_dict(
            "SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id",
            values
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return serialize_workflow(existing)

    query = f"""
        UPDATE workflows
        SET {', '.join(set_clauses)}
        WHERE id = :id AND tenant_id = :tenant_id
        RETURNING *
    """

    updated_workflow = await fetch_one_dict(query, values)
    if not updated_workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return serialize_workflow(updated_workflow)

@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    workflow_uuid = validate_uuid(workflow_id)
    query = "DELETE FROM workflows WHERE id = :id AND tenant_id = :tenant_id"
    values = {"id": str(workflow_uuid), "tenant_id": current_user["tenant_id"]}
    await execute_dict(query, values)
    return {"message": "Workflow deleted successfully"}

@router.post("/{workflow_id}/activities", response_model=WorkflowActivity)
async def create_workflow_activity(workflow_id: str, activity_data: WorkflowActivityCreate, current_user: Dict[str, Any] = Depends(get_current_user)):
    workflow_uuid = validate_uuid(workflow_id)
    activity_id = str(uuid.uuid4())
    # Handle error handling configuration
    error_handling = {}
    if activity_data.on_error_action:
        error_handling["on_error"] = activity_data.on_error_action
    if activity_data.error_handling:
        error_handling.update(activity_data.error_handling)

    # Set default if no error handling specified
    if not error_handling:
        error_handling = {"on_error": "stop", "retry_count": 0}

    query = """INSERT INTO workflow_activities (id, workflow_id, tenant_id, name, activity_type, order_index, config, error_handling)
               VALUES (:id, :workflow_id, :tenant_id, :name, :activity_type, :order_index, :config, :error_handling) RETURNING *"""
    values = {
        "id": activity_id,
        "workflow_id": str(workflow_uuid),
        "tenant_id": current_user["tenant_id"],
        "name": activity_data.name,
        "activity_type": activity_data.activity_type,
        "order_index": activity_data.order,
        "config": json.dumps(activity_data.config) if activity_data.config else "{}",
        "error_handling": json.dumps(error_handling)
    }
    new_activity = await fetch_one_dict(query, values)
    
    # Convert JSON string fields back to dictionaries for Pydantic models
    for field in ['config', 'input_mapping', 'output_mapping', 'error_handling']:
        if field in new_activity and isinstance(new_activity[field], str):
            try:
                new_activity[field] = json.loads(new_activity[field])
            except (json.JSONDecodeError, TypeError):
                new_activity[field] = {}
    
    return new_activity

@router.get("/{workflow_id}/activities", response_model=List[WorkflowActivity])
async def list_workflow_activities(workflow_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    workflow_uuid = validate_uuid(workflow_id)
    query = 'SELECT * FROM workflow_activities WHERE workflow_id = :workflow_id ORDER BY order_index'
    values = {"workflow_id": str(workflow_uuid)}
    activities = await fetch_all_dict(query, values)

    def _ensure_dict(val):
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    coerced = []
    for a in activities:
        c = dict(a)
        c['config'] = _ensure_dict(a.get('config'))
        c['input_mapping'] = _ensure_dict(a.get('input_mapping'))
        c['output_mapping'] = _ensure_dict(a.get('output_mapping'))
        c['error_handling'] = _ensure_dict(a.get('error_handling'))
        coerced.append(c)

    return coerced

@router.put("/{workflow_id}/activities/{activity_id}", response_model=WorkflowActivity)
async def update_workflow_activity(workflow_id: str, activity_id: str, activity_data: WorkflowActivityUpdate, current_user: Dict[str, Any] = Depends(get_current_user)):

    workflow_uuid = validate_uuid(workflow_id)
    activity_uuid = validate_uuid(activity_id)

    # Build dynamic update query only for provided fields
    update_fields = []
    values = {
        "id": str(activity_uuid),
        "workflow_id": str(workflow_uuid)
    }

    if activity_data.name is not None:
        update_fields.append("name = :name")
        values["name"] = activity_data.name

    if activity_data.activity_type is not None:
        update_fields.append("activity_type = :activity_type")
        values["activity_type"] = activity_data.activity_type

    if activity_data.order is not None:
        update_fields.append("order_index = :order_index")
        values["order_index"] = activity_data.order

    if activity_data.config is not None:
        update_fields.append("config = :config")
        values["config"] = json.dumps(activity_data.config)

    if activity_data.is_enabled is not None:
        update_fields.append("is_enabled = :is_enabled")
        values["is_enabled"] = activity_data.is_enabled

    if not update_fields:
        logger.warning(f"🔧 UPDATE_ACTIVITY: No fields provided for update")
        raise HTTPException(status_code=400, detail="No fields provided for update")

    # Always update the updated_at timestamp
    update_fields.append("updated_at = now()")

    query = f"""UPDATE workflow_activities SET {', '.join(update_fields)}
               WHERE id = :id AND workflow_id = :workflow_id RETURNING *"""


    try:
        updated_activity = await fetch_one_dict(query, values)
    except Exception as e:
        logger.error(f"🔧 UPDATE_ACTIVITY: Database error: {str(e)}")
        raise
    if not updated_activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    
    # Convert JSON string fields back to dictionaries for Pydantic models
    for field in ['config', 'input_mapping', 'output_mapping', 'error_handling']:
        if field in updated_activity and isinstance(updated_activity[field], str):
            try:
                updated_activity[field] = json.loads(updated_activity[field])
            except (json.JSONDecodeError, TypeError):
                updated_activity[field] = {}
    
    return updated_activity

@router.delete("/{workflow_id}/activities/{activity_id}")
async def delete_workflow_activity(workflow_id: str, activity_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    workflow_uuid = validate_uuid(workflow_id)
    activity_uuid = validate_uuid(activity_id)
    query = "DELETE FROM workflow_activities WHERE id = :id AND workflow_id = :workflow_id"
    values = {"id": str(activity_uuid), "workflow_id": str(workflow_uuid)}
    await execute_dict(query, values)
    return {"message": "Activity deleted successfully"}

@router.post("/{workflow_id}/execute")
async def execute_workflow(workflow_id: str, trigger_data: Optional[dict] = None, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Execute a workflow using the enhanced execution service"""
    from services.workflow_execution_service import workflow_execution_service
    import logging
    
    logger = logging.getLogger(__name__)
    workflow_uuid = validate_uuid(workflow_id)
    
    # Check if workflow exists and belongs to tenant
    workflow_query = "SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id"
    workflow_data = await fetch_one_dict(workflow_query, {
        "id": str(workflow_uuid),
        "tenant_id": current_user["tenant_id"]
    })
    
    if not workflow_data:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Prepare trigger data
    if not trigger_data:
        trigger_data = {}

    trigger_data = _normalize_trigger_payload(trigger_data)

    if trigger_data.get("message") or trigger_data.get("raw_message"):
        trigger_data.setdefault("source", "api")
        trigger_data["message"] = trigger_data.get("message") or trigger_data.get("raw_message")
    
    try:
        # Execute workflow using the service
        result = await workflow_execution_service.execute_workflow(
            workflow_id=str(workflow_uuid),
            trigger_data=trigger_data,
            tenant_id=current_user["tenant_id"],
            user_id=current_user["id"]
        )
        
        # Build response with base fields and any activity outputs
        response = {
            "message": "Workflow execution completed",
            "execution_id": result["execution_id"],
            "status": result["status"],
            "execution_time_ms": result.get("execution_time_ms"),
            "activities_executed": result.get("activities_executed", 0),
            "variables": result.get("variables", {})
        }
        
        # Add activity outputs like transformed_message, parsed_message, etc.
        activity_output_keys = [
            "transformed_message", "parsed_message", "parsed_segments", "csv_data",
            "fhir_bundle", "readable_text", "extracted_variables", "loop_results",
            "validation_results", "mapped_data", "converted_data",
            "code_output", "execution_result", "script_output"
        ]
        for key in activity_output_keys:
            if key in result:
                response[key] = result[key]
        
        return response
        
    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {str(e)}")


@router.get("/{workflow_id}/batches/status")
async def get_csv_batch_status(workflow_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Return queued CSV batch status for a workflow, grouped by group_key."""
    workflow_uuid = validate_uuid(workflow_id)
    # Ensure workflow belongs to tenant
    wf = await fetch_one_dict("SELECT id FROM workflows WHERE id = :id AND tenant_id = :tenant_id", {"id": str(workflow_uuid), "tenant_id": current_user["tenant_id"]})
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # If table doesn't exist, return empty
    try:
        rows = await fetch_all_dict(
            """
            SELECT group_key,
                   COUNT(*)::int AS queued_rows,
                   MIN(created_at) AS oldest_created_at
            FROM csv_batch_rows
            WHERE workflow_id = :wid AND tenant_id = :tid AND flushed_at IS NULL
            GROUP BY group_key
            ORDER BY group_key
            """,
            {"wid": str(workflow_uuid), "tid": current_user["tenant_id"]}
        )
    except Exception:
        rows = []

    return {"groups": rows}


class FlushRequest(BaseModel):
    group_key: Optional[str] = None
    key_template: Optional[str] = None
    bucket: Optional[str] = None
    dry_run: bool = False


@router.post("/{workflow_id}/batches/flush")
async def flush_csv_batches(workflow_id: str, req: FlushRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Flush queued CSV batch rows to S3 for a workflow. Optionally limit to a group_key."""
    import json as _json
    from datetime import datetime

    workflow_uuid = validate_uuid(workflow_id)
    wf = await fetch_one_dict("SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id", {"id": str(workflow_uuid), "tenant_id": current_user["tenant_id"]})
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Try load csv_batcher activity config as defaults
    act = await fetch_one_dict(
        "SELECT config FROM workflow_activities WHERE workflow_id = :wid AND activity_type = :type ORDER BY order_index LIMIT 1",
        {"wid": str(workflow_uuid), "type": "csv_batcher"}
    )
    act_cfg = (act or {}).get("config") or {}
    if isinstance(act_cfg, str):
        try:
            act_cfg = _json.loads(act_cfg)
        except Exception:
            act_cfg = {}

    group_tpl = req.key_template or act_cfg.get("s3", {}).get("key_template") or act_cfg.get("group_key_template") or "hl7/{tenant_id}/{date}/workflow-{workflow_id}.csv"
    bucket = req.bucket or act_cfg.get("s3", {}).get("bucket") or s3_service.bucket_name

    # Pick groups
    where = "workflow_id = :wid AND tenant_id = :tid AND flushed_at IS NULL"
    params = {"wid": str(workflow_uuid), "tid": current_user["tenant_id"]}
    if req.group_key:
        where += " AND group_key = :gk"
        params["gk"] = req.group_key

    groups = await fetch_all_dict(f"SELECT DISTINCT group_key FROM csv_batch_rows WHERE {where}", params)

    def substitute(template: str) -> str:
        date_str = datetime.utcnow().strftime('%Y-%m-%d')
        ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        out = template
        subs = {
            'tenant_id': str(current_user['tenant_id']),
            'workflow_id': str(workflow_uuid),
            'date': date_str,
            'timestamp': ts,
        }
        for k, v in subs.items():
            out = out.replace(f'{{{k}}}', v).replace(f'{{{{{k}}}}}', v)
        return out

    results = []
    for g in groups:
        gk = g.get('group_key')
        rows = await fetch_all_dict(
            "SELECT id, headers, row FROM csv_batch_rows WHERE " + where + " AND group_key = :gk ORDER BY created_at ASC",
            {**params, "gk": gk}
        )
        if not rows:
            continue
        # Determine header order
        header_list = []
        h0 = rows[0].get('headers')
        if isinstance(h0, str):
            try:
                h0 = _json.loads(h0)
            except Exception:
                h0 = []
        header_list = h0 if isinstance(h0, list) else []

        # Build CSV
        import csv as _csv, io as _io
        out = _io.StringIO()
        writer = _csv.DictWriter(out, fieldnames=header_list)
        writer.writeheader()
        for r in rows:
            row_obj = r.get('row')
            if isinstance(row_obj, str):
                try:
                    row_obj = _json.loads(row_obj)
                except Exception:
                    row_obj = {}
            writer.writerow({h: row_obj.get(h, '') for h in header_list})
        csv_content = out.getvalue()

        # Resolve S3 key
        base_key = substitute(group_tpl)
        if base_key.endswith('/'):
            base_key += 'batch.csv'
        if not base_key.endswith('.csv'):
            base_key += '.csv'
        key = base_key.replace('.csv', f"_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{len(rows)}.csv")

        if req.dry_run:
            results.append({"group_key": gk, "row_count": len(rows), "s3_bucket": bucket, "s3_key": key, "dry_run": True})
            continue

        # Upload
        try:
            await s3_service.upload_content(bucket=bucket, key=key, content=csv_content, content_type='text/csv', metadata={
                "tenant_id": str(current_user['tenant_id']),
                "workflow_id": str(workflow_uuid),
                "group_key": gk,
                "row_count": str(len(rows))
            })
        except Exception as e:
            results.append({"group_key": gk, "error": f"S3 upload failed: {e}"})
            continue

        # Mark flushed
        await execute_dict(
            "UPDATE csv_batch_rows SET flushed_at = NOW(), flush_key = :key WHERE " + where + " AND group_key = :gk",
            {**params, "gk": gk, "key": key}
        )
        results.append({"group_key": gk, "row_count": len(rows), "s3_bucket": bucket, "s3_key": key})

    return {"flushed": results}


@router.post("/{workflow_id}/batches/flush-stale")
async def flush_stale_csv_batches(
    workflow_id: str,
    max_age_seconds: int = 300,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Flush any groups whose oldest queued row is older than max_age_seconds."""
    from datetime import datetime, timedelta
    import json as _json

    workflow_uuid = validate_uuid(workflow_id)
    wf = await fetch_one_dict("SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id", {"id": str(workflow_uuid), "tenant_id": current_user["tenant_id"]})
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Find groups with oldest row beyond threshold
    cutoff = datetime.utcnow() - timedelta(seconds=max_age_seconds)
    groups = await fetch_all_dict(
        """
        SELECT group_key
        FROM csv_batch_rows
        WHERE tenant_id = :tid AND workflow_id = :wid AND flushed_at IS NULL
        GROUP BY group_key
        HAVING MIN(created_at) <= :cutoff
        """,
        {"tid": current_user["tenant_id"], "wid": str(workflow_uuid), "cutoff": cutoff}
    )

    # Reuse general flush with those keys
    results = []
    for g in groups:
        res = await flush_csv_batches(workflow_id, FlushRequest(group_key=g.get('group_key')), current_user)
        results.extend(res.get('flushed', []))
    return {"flushed": results, "count": len(results)}

@router.get("/executions/{execution_id}/status")
async def get_execution_status(execution_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Get the status of a workflow execution"""
    # Check database for executions
    query = """
        SELECT we.*, w.name as workflow_name
        FROM workflow_executions we
        JOIN workflows w ON we.workflow_id = w.id
        WHERE we.execution_id = :execution_id AND we.tenant_id = :tenant_id
    """
    values = {"execution_id": execution_id, "tenant_id": current_user["tenant_id"]}
    execution = await fetch_one_dict(query, values)
    
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    return {
        "execution_id": execution_id,
        "status": execution["status"].lower(),
        "workflow_id": str(execution["workflow_id"]),
        "workflow_name": execution["workflow_name"],
        "started_at": execution["started_at"].isoformat() if execution["started_at"] else None,
        "completed_at": execution["completed_at"].isoformat() if execution["completed_at"] else None,
        "execution_time_ms": execution["execution_time_ms"],
        "error_message": execution["error_message"],
        "result": execution.get("result", {}),
        "execution_log": execution.get("execution_log", [])
    }

@router.get("/{workflow_id}/executions")
async def list_workflow_executions(
    workflow_id: str, 
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """List executions for a specific workflow"""
    workflow_uuid = validate_uuid(workflow_id)
    
    # Check if workflow exists and belongs to tenant
    workflow_query = "SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id"
    workflow_data = await fetch_one_dict(workflow_query, {
        "id": str(workflow_uuid),
        "tenant_id": current_user["tenant_id"]
    })
    
    if not workflow_data:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Get executions
    query = """
        SELECT we.*, w.name as workflow_name
        FROM workflow_executions we
        JOIN workflows w ON we.workflow_id = w.id
        WHERE we.workflow_id = :workflow_id AND we.tenant_id = :tenant_id
        ORDER BY we.started_at DESC
        LIMIT :limit OFFSET :offset
    """
    values = {
        "workflow_id": str(workflow_uuid),
        "tenant_id": current_user["tenant_id"],
        "limit": limit,
        "offset": offset
    }
    executions = await fetch_all_dict(query, values)
    
    # Get total count
    count_query = """
        SELECT COUNT(*) as total
        FROM workflow_executions we
        WHERE we.workflow_id = :workflow_id AND we.tenant_id = :tenant_id
    """
    count_result = await fetch_one_dict(count_query, {
        "workflow_id": str(workflow_uuid),
        "tenant_id": current_user["tenant_id"]
    })
    total = count_result["total"] if count_result else 0
    
    return {
        "executions": executions,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(executions) < total
        }
    }

class PromoteRequest(BaseModel):
    target_tenant_id: str

@router.post("/{workflow_id}/promote")
async def promote_workflow(
    workflow_id: str,
    body: PromoteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Copy a workflow and its activities to another tenant (promotion)."""
    import uuid as _uuid
    import json as _json

    wf_uuid = validate_uuid(workflow_id)
    try:
        target_tid = _uuid.UUID(body.target_tenant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid target_tenant_id")

    # Verify source workflow exists in current tenant
    src_wf = await fetch_one_dict("SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id", {
        'id': str(wf_uuid), 'tenant_id': current_user['tenant_id']
    })
    if not src_wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Verify user has membership in target tenant
    my_tenants = await TenantRepository.get_tenants_for_user(current_user['id'] if isinstance(current_user['id'], _uuid.UUID) else _uuid.UUID(str(current_user['id'])))
    if not any(str(t['id']) == str(target_tid) for t in my_tenants):
        raise HTTPException(status_code=403, detail="No access to target tenant")

    # Fetch activities
    activities = await fetch_all_dict('SELECT * FROM workflow_activities WHERE workflow_id = :wid ORDER BY order_index', { 'wid': str(wf_uuid) })

    # Insert new workflow in target
    new_id = str(_uuid.uuid4())
    create_q = """
        INSERT INTO workflows (id, tenant_id, created_by_id, name, description, version, status, execution_mode, settings, environment_variables)
        VALUES (:id, :tenant_id, :created_by_id, :name, :description, :version, 'DRAFT', :execution_mode, :settings, :env)
        RETURNING *
    """
    values = {
        'id': new_id,
        'tenant_id': str(target_tid),
        'created_by_id': str(current_user['id']),
        'name': src_wf.get('name'),
        'description': src_wf.get('description'),
        'version': src_wf.get('version') or '1.0.0',
        'execution_mode': src_wf.get('execution_mode') or 'REAL_TIME',
        'settings': _json.dumps(src_wf.get('settings') or {}),
        'env': _json.dumps(src_wf.get('environment_variables') or {}),
    }
    new_wf = await fetch_one_dict(create_q, values)

    # Copy activities
    for act in activities:
        await fetch_one_dict(
            """
            INSERT INTO workflow_activities (id, workflow_id, tenant_id, name, activity_type, description, order_index, is_enabled, config, input_mapping, output_mapping, error_handling)
            VALUES (:id, :workflow_id, :tenant_id, :name, :activity_type, :description, :order_index, :is_enabled, :config, :input_mapping, :output_mapping, :error_handling)
            RETURNING id
            """,
            {
                'id': str(_uuid.uuid4()),
                'workflow_id': new_id,
                'tenant_id': str(target_tid),
                'name': act.get('name'),
                'activity_type': act.get('activity_type'),
                'description': act.get('description'),
                'order_index': act.get('order_index') or 0,
                'is_enabled': act.get('is_enabled', True),
                'config': _json.dumps(act.get('config') or {}),
                'input_mapping': _json.dumps(act.get('input_mapping') or {}),
                'output_mapping': _json.dumps(act.get('output_mapping') or {}),
                'error_handling': _json.dumps(act.get('error_handling') or { 'on_error': 'stop', 'retry_count': 0 }),
            }
        )

    return {
        'message': 'Workflow promoted',
        'source_workflow_id': str(wf_uuid),
        'target_workflow_id': new_id,
        'target_tenant_id': str(target_tid)
    }

@router.post("/{workflow_id}/test")
async def test_workflow(
    workflow_id: str, 
    test_data: dict,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Test a workflow with sample data"""
    from services.workflow_execution_service import workflow_execution_service
    
    workflow_uuid = validate_uuid(workflow_id)
    
    # Check if workflow exists and belongs to tenant
    workflow_query = "SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id"
    workflow_data = await fetch_one_dict(workflow_query, {
        "id": str(workflow_uuid),
        "tenant_id": current_user["tenant_id"]
    })
    
    if not workflow_data:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Normalize payload and add test metadata
    test_data = _normalize_trigger_payload(test_data)
    test_data["source"] = "test"
    test_data["is_test"] = True
    
    try:
        # Execute workflow using the service
        result = await workflow_execution_service.execute_workflow(
            workflow_id=str(workflow_uuid),
            trigger_data=test_data,
            tenant_id=current_user["tenant_id"],
            user_id=current_user["id"]
        )
        
        return {
            "message": "Workflow test completed",
            "execution_id": result["execution_id"],
            "status": result["status"],
            "execution_time_ms": result.get("execution_time_ms"),
            "activities_executed": result.get("activities_executed", 0),
            "variables": result.get("variables", {}),
            "test_mode": True
        }
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Workflow test failed: {e}")
        raise HTTPException(status_code=500, detail=f"Workflow test failed: {str(e)}")

@router.get("/executions/{execution_id}/activities")
async def get_execution_activities(
    execution_id: str, 
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Get activity executions for a specific workflow execution"""
    # Get workflow execution to verify access
    workflow_execution_query = """
        SELECT we.*, w.name as workflow_name
        FROM workflow_executions we
        JOIN workflows w ON we.workflow_id = w.id
        WHERE we.execution_id = :execution_id AND we.tenant_id = :tenant_id
    """
    workflow_execution = await fetch_one_dict(workflow_execution_query, {
        "execution_id": execution_id,
        "tenant_id": current_user["tenant_id"]
    })
    
    if not workflow_execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    # Get activity executions
    activity_executions_query = """
        SELECT ae.*, wa.name as activity_name, wa.activity_type
        FROM activity_executions ae
        JOIN workflow_activities wa ON ae.activity_id = wa.id
        WHERE ae.workflow_execution_id = :workflow_execution_id
        ORDER BY ae.sequence_order
    """
    activity_executions = await fetch_all_dict(activity_executions_query, {
        "workflow_execution_id": workflow_execution["id"]
    })
    
    # Parse JSON fields
    for execution in activity_executions:
        for field in ['input_data', 'output_data']:
            if field in execution and isinstance(execution[field], str):
                try:
                    execution[field] = json.loads(execution[field])
                except:
                    execution[field] = {}
    
    return {
        "execution_id": execution_id,
        "workflow_name": workflow_execution["workflow_name"],
        "activity_executions": activity_executions
    }
class MappingPreviewRequest(BaseModel):
    raw_message: str
    mappings: List[Dict[str, Any]]

@router.post("/mapping/preview")
async def mapping_preview(req: MappingPreviewRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Preview data mapper output for a sample HL7 message and mappings config."""
    from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus
    from processors.file_processors import process_data_mapper_activity
    # Build a minimal context
    ctx = WorkflowContext(
        workflow_id=str(uuid.uuid4()),
        execution_id=str(uuid.uuid4()),
        tenant_id=str(current_user.get('tenant_id')),
        variables={ 'source': 'preview' },
        raw_message=req.raw_message,
        execution_log=[]
    )
    activity = { 'name': 'mapping-preview', 'config': { 'mappings': req.mappings } }
    try:
        res: ActivityResult = await process_data_mapper_activity(activity, ctx)
        return { 'mapped': res.output_data.get('mapped'), 'variables': res.variables }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Preview failed: {e}")


@router.get("/{workflow_id}/export")
async def export_workflow(workflow_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Export a workflow with all its activities as a JSON file.
    Returns a downloadable JSON file containing the complete workflow definition.
    """
    import uuid as _uuid
    from datetime import datetime

    workflow_uuid = validate_uuid(workflow_id)

    # Get workflow
    workflow_query = "SELECT * FROM workflows WHERE id = :id AND tenant_id = :tenant_id"
    workflow = await fetch_one_dict(workflow_query, {
        "id": str(workflow_uuid),
        "tenant_id": current_user["tenant_id"]
    })

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get all activities for this workflow
    activities_query = "SELECT * FROM workflow_activities WHERE workflow_id = :workflow_id ORDER BY order_index"
    activities = await fetch_all_dict(activities_query, {"workflow_id": str(workflow_uuid)})

    # Parse JSON fields in activities
    def _ensure_dict(val):
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    parsed_activities = []
    for activity in activities:
        activity_data = dict(activity)
        # Remove internal fields
        for field in ['id', 'workflow_id', 'tenant_id', 'created_at', 'updated_at']:
            activity_data.pop(field, None)

        # Parse JSON fields
        activity_data['config'] = _ensure_dict(activity.get('config'))
        activity_data['input_mapping'] = _ensure_dict(activity.get('input_mapping'))
        activity_data['output_mapping'] = _ensure_dict(activity.get('output_mapping'))
        activity_data['error_handling'] = _ensure_dict(activity.get('error_handling'))

        parsed_activities.append(activity_data)

    # Build export data
    export_data = {
        "version": "1.0",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "workflow": {
            "name": workflow.get("name"),
            "description": workflow.get("description"),
            "version": workflow.get("version", "1.0.0"),
            "execution_mode": workflow.get("execution_mode", "REAL_TIME"),
            "settings": _ensure_dict(workflow.get("settings")),
            "environment_variables": _ensure_dict(workflow.get("environment_variables")),
            "max_concurrent_executions": workflow.get("max_concurrent_executions"),
            "timeout_seconds": workflow.get("timeout_seconds"),
            "retry_attempts": workflow.get("retry_attempts"),
            "cron_expression": workflow.get("cron_expression")
        },
        "activities": parsed_activities
    }

    # Return as JSON response with content-disposition header
    from fastapi.responses import JSONResponse

    filename = f"workflow-{workflow.get('name', 'export')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.json"

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/json"
        }
    )


class WorkflowImportRequest(BaseModel):
    workflow_data: Dict[str, Any]
    name_override: Optional[str] = None


@router.post("/import", response_model=Workflow)
async def import_workflow(import_data: WorkflowImportRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Import a workflow from an exported JSON file.
    Creates a new workflow with all its activities in the current tenant.
    """
    import uuid as _uuid

    def serialize_workflow(workflow: dict) -> dict:
        # Convert UUID fields to strings
        for key in ["id", "tenant_id", "created_by_id"]:
            if key in workflow and isinstance(workflow[key], _uuid.UUID):
                workflow[key] = str(workflow[key])
        # Ensure dict fields are valid dictionaries
        for key in ["settings", "environment_variables"]:
            if key in workflow and not isinstance(workflow[key], dict):
                workflow[key] = {}
        return workflow

    workflow_data = import_data.workflow_data

    # Validate structure
    if not workflow_data.get("workflow"):
        raise HTTPException(status_code=400, detail="Invalid workflow export format: missing 'workflow' key")

    if not workflow_data.get("activities"):
        raise HTTPException(status_code=400, detail="Invalid workflow export format: missing 'activities' key")

    wf = workflow_data["workflow"]
    activities = workflow_data["activities"]

    # Create new workflow
    new_workflow_id = str(_uuid.uuid4())
    workflow_name = import_data.name_override or wf.get("name", "Imported Workflow")

    create_workflow_query = """
        INSERT INTO workflows (
            id, tenant_id, created_by_id, name, description, version,
            status, execution_mode, settings, environment_variables,
            max_concurrent_executions, timeout_seconds, retry_attempts, cron_expression
        )
        VALUES (
            :id, :tenant_id, :created_by_id, :name, :description, :version,
            'DRAFT', :execution_mode, :settings, :environment_variables,
            :max_concurrent_executions, :timeout_seconds, :retry_attempts, :cron_expression
        )
        RETURNING *
    """

    workflow_values = {
        "id": new_workflow_id,
        "tenant_id": current_user["tenant_id"],
        "created_by_id": current_user["id"],
        "name": workflow_name,
        "description": wf.get("description"),
        "version": wf.get("version", "1.0.0"),
        "execution_mode": wf.get("execution_mode", "REAL_TIME"),
        "settings": json.dumps(wf.get("settings", {})),
        "environment_variables": json.dumps(wf.get("environment_variables", {})),
        "max_concurrent_executions": wf.get("max_concurrent_executions"),
        "timeout_seconds": wf.get("timeout_seconds"),
        "retry_attempts": wf.get("retry_attempts"),
        "cron_expression": wf.get("cron_expression")
    }

    new_workflow = await fetch_one_dict(create_workflow_query, workflow_values)

    # Create activities
    for activity in activities:
        activity_id = str(_uuid.uuid4())

        create_activity_query = """
            INSERT INTO workflow_activities (
                id, workflow_id, tenant_id, name, activity_type, description,
                order_index, is_enabled, config, input_mapping, output_mapping, error_handling
            )
            VALUES (
                :id, :workflow_id, :tenant_id, :name, :activity_type, :description,
                :order_index, :is_enabled, :config, :input_mapping, :output_mapping, :error_handling
            )
            RETURNING id
        """

        activity_values = {
            "id": activity_id,
            "workflow_id": new_workflow_id,
            "tenant_id": current_user["tenant_id"],
            "name": activity.get("name"),
            "activity_type": activity.get("activity_type"),
            "description": activity.get("description"),
            "order_index": activity.get("order_index", 0),
            "is_enabled": activity.get("is_enabled", True),
            "config": json.dumps(activity.get("config", {})),
            "input_mapping": json.dumps(activity.get("input_mapping", {})),
            "output_mapping": json.dumps(activity.get("output_mapping", {})),
            "error_handling": json.dumps(activity.get("error_handling", {"on_error": "stop", "retry_count": 0}))
        }

        await fetch_one_dict(create_activity_query, activity_values)

    logger.info(f"Imported workflow {new_workflow_id} with {len(activities)} activities for tenant {current_user['tenant_id']}")

    return serialize_workflow(new_workflow)
