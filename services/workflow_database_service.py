"""
Workflow Database Service
Handles all database operations for workflow execution and activity tracking
"""
import json
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field

from database.connection import fetch_one_dict, fetch_all_dict, execute_dict
from services.hl7_parser import ParsedHL7Message


@dataclass
class WorkflowContext:
    """Context passed between workflow activities"""
    workflow_id: str
    execution_id: str
    tenant_id: str
    variables: Dict[str, Any] = field(default_factory=dict)
    message: Optional[ParsedHL7Message] = None
    raw_message: Optional[str] = None
    execution_log: List[Dict[str, Any]] = field(default_factory=list)
    current_activity: Optional[str] = None
    errors: List[str] = field(default_factory=list)


async def get_workflow(workflow_id: str) -> Optional[Dict[str, Any]]:
    """Get workflow from database"""
    query = "SELECT * FROM workflows WHERE id = :workflow_id"
    return await fetch_one_dict(query, {"workflow_id": workflow_id})


async def get_workflow_activities(workflow_id: str) -> List[Dict[str, Any]]:
    """Get workflow activities from database"""
    query = """
        SELECT * FROM workflow_activities
        WHERE workflow_id = :workflow_id
        ORDER BY order_index
    """
    activities = await fetch_all_dict(query, {"workflow_id": workflow_id})

    # Parse JSON fields
    for activity in activities:
        for field in ['config', 'input_mapping', 'output_mapping', 'error_handling']:
            if field in activity and isinstance(activity[field], str):
                try:
                    activity[field] = json.loads(activity[field])
                except:
                    activity[field] = {}

    return activities


async def create_execution_record(
    execution_id: str,
    workflow_id: str,
    tenant_id: str,
    user_id: Optional[str],
    trigger_data: Dict[str, Any]
):
    """Create workflow execution record in database"""
    query = """
        INSERT INTO workflow_executions (
            id, workflow_id, tenant_id, execution_id, trigger_type, triggered_by,
            status, started_at, execution_log, debug_info
        ) VALUES (
            :id, :workflow_id, :tenant_id, :execution_id, :trigger_type, :triggered_by,
            :status, :started_at, :execution_log, :debug_info
        )
    """

    values = {
        "id": str(uuid.uuid4()),
        "workflow_id": str(workflow_id),
        "tenant_id": str(tenant_id),
        "execution_id": str(execution_id),
        "trigger_type": trigger_data.get("source", "manual"),
        "triggered_by": str(user_id) if user_id else "system",
        "status": "RUNNING",
        "started_at": datetime.utcnow(),
        "execution_log": json.dumps([]),
        "debug_info": json.dumps({"trigger_data": trigger_data})
    }

    await execute_dict(query, values)


async def update_execution_record(
    execution_id: str,
    status: str,
    completed_at: Optional[datetime] = None,
    execution_time_ms: Optional[int] = None,
    result: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    execution_log: Optional[List[Dict[str, Any]]] = None
):
    """Update workflow execution record"""
    query = """
        UPDATE workflow_executions SET
            status = :status,
            completed_at = :completed_at,
            execution_time_ms = :execution_time_ms,
            result = :result,
            error_message = :error_message,
            execution_log = :execution_log
        WHERE execution_id = :execution_id
    """

    values = {
        "execution_id": str(execution_id),
        "status": status,
        "completed_at": completed_at,
        "execution_time_ms": execution_time_ms,
        "result": json.dumps(result) if result else None,
        "error_message": error_message,
        "execution_log": json.dumps(execution_log) if execution_log else None
    }

    await execute_dict(query, values)


async def create_activity_execution_record(
    execution_id: str,
    activity: Dict[str, Any],
    started_at: datetime,
    context: Optional[WorkflowContext] = None
) -> str:
    """Create activity execution record in database"""
    activity_execution_id = str(uuid.uuid4())

    # Get workflow execution ID from execution_id
    workflow_execution_query = """
        SELECT id FROM workflow_executions WHERE execution_id = :execution_id
    """
    workflow_execution = await fetch_one_dict(workflow_execution_query, {"execution_id": execution_id})

    if not workflow_execution:
        raise Exception(f"Workflow execution not found for execution_id: {execution_id}")

    # Prepare input data with context information
    input_data = {}
    if context:
        input_data = {
            "raw_message": context.raw_message,
            "variables": context.variables.copy(),
            "current_activity": context.current_activity,
            "activity_type": activity.get("activity_type"),
            "activity_name": activity.get("name")
        }
        # Filter out None values to keep data clean
        input_data = {k: v for k, v in input_data.items() if v is not None}

    query = """
        INSERT INTO activity_executions (
            id, workflow_execution_id, activity_id, sequence_order,
            status, started_at, input_data
        ) VALUES (
            :id, :workflow_execution_id, :activity_id, :sequence_order,
            :status, :started_at, :input_data
        )
    """

    values = {
        "id": activity_execution_id,
        "workflow_execution_id": str(workflow_execution["id"]),
        "activity_id": str(activity["id"]),
        "sequence_order": activity.get("order_index", 0),
        "status": "RUNNING",
        "started_at": started_at,
        "input_data": json.dumps(input_data)
    }

    await execute_dict(query, values)
    return activity_execution_id


async def update_activity_execution_record(
    activity_execution_id: str,
    status: str,
    completed_at: datetime,
    execution_time_ms: int,
    output_data: Dict[str, Any],
    error_message: Optional[str]
):
    """Update activity execution record"""
    query = """
        UPDATE activity_executions SET
            status = :status,
            completed_at = :completed_at,
            execution_time_ms = :execution_time_ms,
            output_data = :output_data,
            error_message = :error_message
        WHERE id = :id
    """

    values = {
        "id": str(activity_execution_id),
        "status": status,
        "completed_at": completed_at,
        "execution_time_ms": execution_time_ms,
        "output_data": json.dumps(output_data),
        "error_message": error_message
    }

    await execute_dict(query, values)