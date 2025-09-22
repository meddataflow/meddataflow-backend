"""
Enhanced Workflow Execution Service
Implements the main goal workflow pattern with comprehensive activity processors
"""
import asyncio
import json
import uuid
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import logging
import re

# Import models
from models.workflow_models import ActivityStatus, WorkflowContext, ActivityResult

# Import services
from services.hl7_parser import HL7Parser, ParsedHL7Message
from services.workflow_database_service import (
    get_workflow, get_workflow_activities, create_execution_record,
    update_execution_record, create_activity_execution_record,
    update_activity_execution_record
)

# Import utility functions
from utils.workflow_utils import extract_hl7_value, evaluate_condition, map_gender, format_date

# Import processors
from processors.core_processors import (
    process_filter_activity, process_transform_activity,
    process_csv_converter_activity, process_s3_storage_activity
)
from processors.gcs_storage_processor import process_gcs_storage_activity
from processors.database_processors import (
    process_database_write_activity
)
from processors.communication_processors import (
    process_http_sender_activity, process_email_sender_activity,
    process_databricks_sender_activity, process_tcp_sender_activity
)
from processors.file_processors import (
    process_file_writer_activity, process_json_converter_activity,
    process_xml_converter_activity, process_pipe_converter_activity,
    process_message_transformer_activity, process_format_converter_activity,
    process_data_mapper_activity, process_csv_batcher_activity
)
from processors.hl7_processors import (
    process_hl7_parser_activity, process_hl7_transformer_activity,
    process_hl7_to_fhir_activity, process_hl7_to_csv_activity,
    process_segment_loop_activity
)
from processors.control_processors import (
    process_validation_activity, process_condition_activity,
    process_loop_activity, process_delay_activity, process_custom_code_activity
)
from processors.emr_sender_processors import (
    process_ecw_fhir_sender_activity, process_nextgen_api_sender_activity,
    process_cerner_fhir_sender_activity, process_epic_hl7_sender_activity
)

logger = logging.getLogger(__name__)


class WorkflowExecutionService:

    def _is_phi_field(self, field_name: str) -> bool:
        """Check if a field name might contain PHI data"""
        phi_indicators = [
            'patient', 'name', 'first_name', 'last_name', 'address', 'phone', 'email',
            'ssn', 'mrn', 'dob', 'birth', 'age', 'sex', 'gender', 'race', 'ethnicity',
            'insurance', 'account', 'diagnosis', 'procedure', 'medication', 'allergy',
            'contact', 'emergency', 'employer', 'guardian', 'next_of_kin', 'raw_message',
            'hl7_message', 'parsed_message', 'message_content', 'pid', 'pv1', 'nk1',
            'al1', 'dg1', 'pr1', 'in1', 'gt1', 'obx', 'nte'
        ]
        field_lower = field_name.lower()
        return any(indicator in field_lower for indicator in phi_indicators)

    """
    Service for executing workflows with comprehensive activity processors
    Implements the main goal pattern: Receiver → Filter → Transform → CSV → S3
    """

    def __init__(self):
        self.hl7_parser = HL7Parser()
        self.activity_processors = self._register_activity_processors()
        self.running_executions = {}

    def _register_activity_processors(self) -> Dict[str, callable]:
        """Register all activity type processors"""
        return {
            # Core activities from main goal
            "filter": process_filter_activity,
            "transform": process_transform_activity,
            "csv_converter": process_csv_converter_activity,
            "s3_storage": process_s3_storage_activity,
            "gcs_storage": process_gcs_storage_activity,
            "csv_batcher": process_csv_batcher_activity,

            # Additional supported activity types
            "http_sender": process_http_sender_activity,
            "database_write": process_database_write_activity,
            "file_writer": process_file_writer_activity,
            "email_sender": process_email_sender_activity,
            "databricks_sender": process_databricks_sender_activity,
            "tcp_sender": process_tcp_sender_activity,
            "message_transformer": process_message_transformer_activity,
            "format_converter": process_format_converter_activity,
            "data_mapper": process_data_mapper_activity,
            "custom_code": process_custom_code_activity,
            "validation": process_validation_activity,
            "condition": process_condition_activity,
            "loop": process_loop_activity,
            "delay": process_delay_activity,
            "json_converter": process_json_converter_activity,
            "xml_converter": process_xml_converter_activity,
            "pipe_separated_converter": process_pipe_converter_activity,
            # Frontend alias
            "pipe_converter": process_pipe_converter_activity,

            # HL7 Specific Activities (per use case requirements)
            "hl7_parser": process_hl7_parser_activity,
            "hl7_transformer": process_hl7_transformer_activity,
            "hl7_to_fhir": process_hl7_to_fhir_activity,
            "hl7_to_csv": process_hl7_to_csv_activity,
            "segment_loop": process_segment_loop_activity,

            # EMR Sender Activities
            "ecw_fhir_sender": process_ecw_fhir_sender_activity,
            "nextgen_api_sender": process_nextgen_api_sender_activity,
            "cerner_fhir_sender": process_cerner_fhir_sender_activity,
            "epic_hl7_sender": process_epic_hl7_sender_activity,
        }

    async def execute_workflow(
        self,
        workflow_id: str,
        trigger_data: Dict[str, Any],
        tenant_id: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a complete workflow"""
        execution_id = str(uuid.uuid4())
        started_at = datetime.utcnow()

        try:
            # Create execution context
            context = WorkflowContext(
                workflow_id=workflow_id,
                execution_id=execution_id,
                tenant_id=tenant_id,
                variables=trigger_data.copy(),
                raw_message=trigger_data.get("message"),
                execution_log=[]
            )

            # Store execution in tracking
            self.running_executions[execution_id] = {
                "status": "RUNNING",
                "started_at": started_at,
                "context": context
            }

            # Get workflow and activities
            workflow = await get_workflow(workflow_id)
            if not workflow:
                raise Exception(f"Workflow {workflow_id} not found")

            activities = await get_workflow_activities(workflow_id)

            # Create execution record in database
            await create_execution_record(
                execution_id, workflow_id, tenant_id, user_id, trigger_data
            )

            # Execute activities in sequence
            for activity in activities:
                if not activity.get("is_enabled", True):
                    self._log_activity_skip(context, activity)
                    continue

                # Check if this activity should be skipped due to condition action
                logger.info(f"🔧 WORKFLOW_EXEC: Checking skip flag for activity '{activity['name']}', skip_next_activity = {context.variables.get('skip_next_activity')}")
                if context.variables.get("skip_next_activity"):
                    logger.info(f"🔧 WORKFLOW_EXEC: Skipping activity '{activity['name']}' due to condition action")
                    context.variables["skip_next_activity"] = False  # Reset flag
                    continue

                context.current_activity = activity["name"]
                safe_starting_keys = [k for k in context.variables.keys() if not self._is_phi_field(k)]
                logger.info(f"🔧 WORKFLOW_EXEC: Starting activity '{activity['name']}' with {len(context.variables)} variables: {safe_starting_keys}")
                result = await self._execute_activity(activity, context)

                # Update context with results - PHI-safe logging
                safe_var_keys = [k for k in result.variables.keys() if not self._is_phi_field(k)]
                logger.info(f"🔧 WORKFLOW_EXEC: Activity '{activity['name']}' returned {len(result.variables)} variables: {safe_var_keys}")
                context.variables.update(result.variables)
                safe_context_keys = [k for k in context.variables.keys() if not self._is_phi_field(k)]
                logger.info(f"🔧 WORKFLOW_EXEC: Context has {len(context.variables)} variables: {safe_context_keys}")

                # Log activity execution
                self._log_activity_execution(context, activity, result)

                # Check for stop workflow flag from condition activities
                if context.variables.get("stop_workflow"):
                    logger.info(f"Stopping workflow due to condition action")
                    break

                # Handle errors based on activity configuration
                if result.status == ActivityStatus.FAILED:
                    # Check both old and new formats for error handling
                    error_action = activity.get("on_error_action")
                    logger.info(f"Activity {activity['name']} on_error_action: {error_action}")

                    if not error_action:
                        # Check nested error_handling format
                        error_handling = activity.get("error_handling", {})
                        error_action = error_handling.get("on_error", "stop")
                        logger.info(f"Activity {activity['name']} error_handling: {error_handling}")
                        logger.info(f"Activity {activity['name']} fallback error_handling.on_error: {error_action}")

                    logger.info(f"Activity {activity['name']} failed with final error action: {error_action}")

                    if error_action == "stop":
                        raise Exception(f"Activity {activity['name']} failed: {result.error_message}")
                    elif error_action == "continue":
                        logger.warning(f"Activity {activity['name']} failed but continuing: {result.error_message}")
                    else:
                        logger.warning(f"Activity {activity['name']} failed, unknown action '{error_action}', continuing")

            # Complete execution
            completed_at = datetime.utcnow()
            execution_time_ms = int((completed_at - started_at).total_seconds() * 1000)

            await update_execution_record(
                execution_id,
                status="COMPLETED",
                completed_at=completed_at,
                execution_time_ms=execution_time_ms,
                result={"variables": context.variables},
                execution_log=context.execution_log
            )

            # Collect important outputs from activities for API response
            activity_outputs = {}
            for log_entry in context.execution_log:
                if log_entry.get("output_data") and log_entry["status"] == "completed":
                    output_data = log_entry["output_data"]
                    if isinstance(output_data, dict):
                        # Include all important activity output keys
                        important_keys = [
                            "transformed_message", "parsed_message", "parsed_segments",
                            "csv_data", "fhir_bundle", "readable_text", "extracted_variables",
                            "loop_results", "validation_results", "mapped_data", "converted_data",
                            "code_output", "execution_result", "script_output"
                        ]
                        for key in important_keys:
                            if key in output_data:
                                activity_outputs[key] = output_data[key]

            result = {
                "execution_id": execution_id,
                "status": "COMPLETED",
                "execution_time_ms": execution_time_ms,
                "variables": context.variables,
                "activities_executed": len([log for log in context.execution_log if log["status"] != "skipped"]),
                "activities_skipped": len([log for log in context.execution_log if log["status"] == "skipped"]),
                "execution_log": context.execution_log,
                **activity_outputs  # Include all collected activity outputs
            }

            # Clean up tracking
            if execution_id in self.running_executions:
                del self.running_executions[execution_id]

            return result

        except Exception as e:
            # Log error
            logger.error(f"Workflow execution failed: {e}")

            # Update execution record with failure
            await update_execution_record(
                execution_id,
                status="FAILED",
                completed_at=datetime.utcnow(),
                execution_time_ms=int((datetime.utcnow() - started_at).total_seconds() * 1000),
                error_message=str(e),
                execution_log=context.execution_log if 'context' in locals() else []
            )

            # Clean up tracking
            if execution_id in self.running_executions:
                del self.running_executions[execution_id]

            return {
                "execution_id": execution_id,
                "status": "FAILED",
                "error": str(e),
                "execution_time_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
                "execution_log": context.execution_log if 'context' in locals() else []
            }

    async def _execute_activity(
        self,
        activity: Dict[str, Any],
        context: WorkflowContext
    ) -> ActivityResult:
        """Execute a single activity"""
        activity_type = activity.get("activity_type", "").lower()
        # Normalize JSON-like fields that might be stored as strings
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
        activity['config'] = _ensure_dict(activity.get('config'))
        activity['input_mapping'] = _ensure_dict(activity.get('input_mapping'))
        activity['output_mapping'] = _ensure_dict(activity.get('output_mapping'))
        activity['error_handling'] = _ensure_dict(activity.get('error_handling'))

        processor = self.activity_processors.get(activity_type)

        if not processor:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=f"No processor for activity type: {activity_type}"
            )

        start_time = datetime.utcnow()

        # Create activity execution record
        activity_execution_id = await create_activity_execution_record(
            context.execution_id, activity, start_time, context
        )

        try:
            # Add debugging for HL7 and database activities - PHI-safe logging
            if activity_type in ['hl7_parser', 'hl7_transformer', 'hl7_to_csv', 'hl7_to_fhir', 'database_write']:
                logger.info(f"Executing activity: {activity_type}, raw_message present: {bool(context.raw_message)}")
                if context.raw_message:
                    logger.info(f"Raw message length: {len(context.raw_message)}")
                safe_var_keys = [k for k in context.variables.keys() if not self._is_phi_field(k)]
                logger.info(f"Context variables (PHI-safe): {safe_var_keys}")

            result = await processor(activity, context)

            # Add debugging for results - PHI-safe logging
            if activity_type in ['hl7_parser', 'hl7_transformer', 'hl7_to_csv', 'hl7_to_fhir', 'database_write']:
                logger.info(f"Activity {activity_type} result: {result.status}, error: {result.error_message}")
                if result.variables:
                    safe_result_keys = [k for k in result.variables.keys() if not self._is_phi_field(k)]
                    logger.info(f"Variables after {activity_type} (PHI-safe): {safe_result_keys}")

            end_time = datetime.utcnow()
            result.execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

            # Update activity execution record with success
            await update_activity_execution_record(
                activity_execution_id, result.status.value, end_time,
                result.execution_time_ms, result.output_data, None
            )

            return result

        except Exception as e:
            end_time = datetime.utcnow()
            execution_time_ms = int((end_time - start_time).total_seconds() * 1000)

            # Update activity execution record with failure
            await update_activity_execution_record(
                activity_execution_id, ActivityStatus.FAILED.value, end_time,
                execution_time_ms, {}, str(e)
            )

            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=str(e),
                execution_time_ms=execution_time_ms
            )

    def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get status of running execution"""
        return self.running_executions.get(execution_id)

    def stop_execution(self, execution_id: str) -> bool:
        """Stop a running execution"""
        if execution_id in self.running_executions:
            # Mark as stopped (in a real implementation, you'd need to handle cancellation)
            self.running_executions[execution_id]["status"] = "STOPPED"
            return True
        return False

    def _log_activity_execution(
        self,
        context: WorkflowContext,
        activity: Dict[str, Any],
        result: ActivityResult
    ):
        """Log activity execution results"""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "activity": activity["name"],
            "activity_type": activity.get("activity_type"),
            "status": result.status.value,
            "execution_time_ms": result.execution_time_ms,
            "output_data": result.output_data,
            "variables_updated": list(result.variables.keys()) if result.variables else []
        }

        if result.error_message:
            log_entry["error_message"] = result.error_message

        context.execution_log.append(log_entry)

    def _log_activity_skip(self, context: WorkflowContext, activity: Dict[str, Any]):
        """Log skipped activity"""
        context.execution_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "activity": activity["name"],
            "activity_type": activity.get("activity_type"),
            "status": "skipped",
            "reason": "Activity is disabled"
        })


# Global instance
workflow_execution_service = WorkflowExecutionService()
