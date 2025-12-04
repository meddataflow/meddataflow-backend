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
    process_s3_storage_activity
    # process_csv_converter_activity merged into hl7_to_csv
)
from processors.gcs_storage_processor import process_gcs_storage_activity
from processors.database_processors import (
    process_database_write_activity
)
from processors.communication_processors import (
    process_http_sender_activity, process_email_sender_activity,
    process_databricks_sender_activity, process_tcp_sender_activity,
    process_sqs_producer_activity, process_sqs_consumer_activity
)
from processors.file_processors import (
    process_file_writer_activity, process_json_converter_activity,
    process_xml_converter_activity, process_pipe_converter_activity,
    process_message_transformer_activity, process_format_converter_activity,
    process_data_mapper_activity, process_csv_batcher_activity, process_sftp_fetch_activity,
    process_bigquery_load_activity
)
from processors.hl7_processors import (
    process_hl7_parser_activity, process_hl7_transformer_activity,
    process_hl7_to_fhir_activity, process_hl7_to_csv_activity,
    process_segment_loop_activity, process_hl7_ack_activity,
    process_csv_to_hl7_activity, process_icare_sender_activity,
    process_icare_sftp_sender_activity, process_icare_webservice_sender_activity
)
from processors.control_processors import (
    process_validation_activity, process_condition_activity,
    process_loop_activity, process_delay_activity, process_custom_code_activity,
    process_content_router_activity
)
from processors.emr_sender_processors import (
    process_ecw_fhir_sender_activity, process_nextgen_api_sender_activity,
    process_cerner_fhir_sender_activity, process_epic_hl7_sender_activity
)
from processors.interoperability_processors import (
    process_fhir_parser_activity,
    process_fhir_transformer_activity,
    process_fhir_translator_activity,
    process_fhir_sender_activity,
    process_dicom_parser_activity,
    process_dicom_transformer_activity,
    process_dicom_translator_activity,
    process_dicom_sender_activity,
    process_x12_parser_activity,
    process_x12_transformer_activity,
    process_x12_translator_activity,
    process_x12_sender_activity,
    process_cda_parser_activity,
    process_cda_transformer_activity,
    process_cda_translator_activity,
    process_cda_sender_activity,
    process_ccd_parser_activity,
    process_ccd_transformer_activity,
    process_ccd_translator_activity,
    process_ccd_sender_activity,
    process_ccr_parser_activity,
    process_ccr_transformer_activity,
    process_ccr_translator_activity,
    process_ccr_sender_activity,
    process_terminology_lookup_activity,
    process_terminology_mapper_activity,
    process_terminology_translator_activity,
    process_terminology_publisher_activity
)
# Import NCPDP processors from dedicated module for better parsing
from processors.ncpdp_processor import (
    process_ncpdp_parser_activity,
    process_ncpdp_transformer_activity,
    process_ncpdp_translator_activity,
    process_ncpdp_sender_activity
)
# Import WebSocket manager for real-time updates
from services.websocket_manager import websocket_manager

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
            # "csv_converter": merged into hl7_to_csv activity
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
            "sqs_producer": process_sqs_producer_activity,
            "sqs_consumer": process_sqs_consumer_activity,
            "message_transformer": process_message_transformer_activity,
            "format_converter": process_format_converter_activity,
            "data_mapper": process_data_mapper_activity,
            "bigquery_load": process_bigquery_load_activity,
            "sftp_fetch": process_sftp_fetch_activity,
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
            "csv_to_hl7": process_csv_to_hl7_activity,
            "segment_loop": process_segment_loop_activity,
            "hl7_ack": process_hl7_ack_activity,
            "icare_sender": process_icare_sender_activity,
            "icare_webservice_sender": process_icare_webservice_sender_activity,
            "icare_sftp_sender": process_icare_sftp_sender_activity,

            # EMR Sender Activities
            "ecw_fhir_sender": process_ecw_fhir_sender_activity,
            "nextgen_api_sender": process_nextgen_api_sender_activity,
            "cerner_fhir_sender": process_cerner_fhir_sender_activity,
            "epic_hl7_sender": process_epic_hl7_sender_activity,

            # FHIR Interoperability Activities
            "fhir_parser": process_fhir_parser_activity,
            "fhir_transformer": process_fhir_transformer_activity,
            "fhir_translator": process_fhir_translator_activity,
            "fhir_sender": process_fhir_sender_activity,

            # DICOM Interoperability Activities
            "dicom_parser": process_dicom_parser_activity,
            "dicom_transformer": process_dicom_transformer_activity,
            "dicom_translator": process_dicom_translator_activity,
            "dicom_sender": process_dicom_sender_activity,

            # NCPDP Interoperability Activities
            "ncpdp_parser": process_ncpdp_parser_activity,
            "ncpdp_transformer": process_ncpdp_transformer_activity,
            "ncpdp_translator": process_ncpdp_translator_activity,
            "ncpdp_sender": process_ncpdp_sender_activity,

            # X12 Interoperability Activities
            "x12_parser": process_x12_parser_activity,
            "x12_transformer": process_x12_transformer_activity,
            "x12_translator": process_x12_translator_activity,
            "x12_sender": process_x12_sender_activity,

            # Clinical Document Activities
            "cda_parser": process_cda_parser_activity,
            "cda_transformer": process_cda_transformer_activity,
            "cda_translator": process_cda_translator_activity,
            "cda_sender": process_cda_sender_activity,
            "ccd_parser": process_ccd_parser_activity,
            "ccd_transformer": process_ccd_transformer_activity,
            "ccd_translator": process_ccd_translator_activity,
            "ccd_sender": process_ccd_sender_activity,
            "ccr_parser": process_ccr_parser_activity,
            "ccr_transformer": process_ccr_transformer_activity,
            "ccr_translator": process_ccr_translator_activity,
            "ccr_sender": process_ccr_sender_activity,

            # Terminology Services Activities
            "terminology_lookup": process_terminology_lookup_activity,
            "terminology_mapper": process_terminology_mapper_activity,
            "terminology_translator": process_terminology_translator_activity,
            "terminology_publisher": process_terminology_publisher_activity,
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

            # Send execution started event via WebSocket
            await websocket_manager.send_execution_started(
                execution_id=execution_id,
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                started_at=started_at.isoformat()
            )

            # Execute activities in sequence
            # Skip counter for content-based routing
            if 'skip_next_n' not in context.variables:
                context.variables['skip_next_n'] = 0

            total_activities = len(activities)
            activity_index = 0

            for activity in activities:
                activity_index += 1
                if not activity.get("is_enabled", True):
                    self._log_activity_skip(context, activity)
                    continue

                # Check if this activity should be skipped due to condition action
                if context.variables.get("skip_next_activity"):
                    context.variables["skip_next_activity"] = False  # Reset flag
                    self._log_activity_skip(context, activity, reason="Skipped by condition")
                    continue

                # Honor skip_next_n from routing
                try:
                    remaining = int(context.variables.get("skip_next_n") or 0)
                except Exception:
                    remaining = 0
                if remaining > 0:
                    remaining -= 1
                    context.variables["skip_next_n"] = remaining
                    self._log_activity_skip(context, activity, reason="Skipped by routing")
                    continue

                context.current_activity = activity["name"]

                # Send activity started event
                await websocket_manager.send_activity_started(
                    execution_id=execution_id,
                    tenant_id=tenant_id,
                    activity_name=activity["name"],
                    activity_type=activity.get("activity_type", ""),
                    activity_index=activity_index
                )

                # Send progress update
                progress_percentage = (activity_index / total_activities) * 100
                await websocket_manager.send_execution_progress(
                    execution_id=execution_id,
                    tenant_id=tenant_id,
                    current_activity=activity_index,
                    total_activities=total_activities,
                    progress_percentage=progress_percentage
                )

                result = await self._execute_activity(activity, context)

                # Update context with results
                context.variables.update(result.variables)

                # Log activity execution
                self._log_activity_execution(context, activity, result)

                # Send activity completed event
                await websocket_manager.send_activity_completed(
                    execution_id=execution_id,
                    tenant_id=tenant_id,
                    activity_name=activity["name"],
                    activity_type=activity.get("activity_type", ""),
                    activity_index=activity_index,
                    execution_time_ms=result.execution_time_ms or 0,
                    status=result.status.value,
                    output_data=result.output_data if result.status == ActivityStatus.COMPLETED else None,
                    error_message=result.error_message if result.status == ActivityStatus.FAILED else None
                )

                # Check for stop workflow flag from condition activities
                if context.variables.get("stop_workflow"):
                    break

                # Handle errors based on activity configuration
                if result.status == ActivityStatus.FAILED:
                    # Check both old and new formats for error handling
                    error_action = activity.get("on_error_action")
                    error_handling = activity.get("error_handling", {}) or {}
                    if not error_action:
                        error_action = error_handling.get("on_error", "stop")
                    retries_cfg = int(error_handling.get("retry_count", 0) or 0)
                    base_delay = int(error_handling.get("retry_delay_ms", 500) or 500)

                    if error_action == "retry" and retries_cfg > 0:
                        # simple inline retry with linear backoff
                        for attempt in range(retries_cfg):
                            try:
                                await asyncio.sleep((attempt + 1) * (base_delay / 1000))
                                retry_result = await self._execute_activity(activity, context)
                                context.variables.update(retry_result.variables)
                                self._log_activity_execution(context, activity, retry_result)
                                if retry_result.status == ActivityStatus.COMPLETED:
                                    break
                            except Exception as _e:
                                # log as failed attempt and continue
                                logger.error(f"Retry attempt {attempt+1} failed for {activity['name']}: {_e}")
                        else:
                            # exhausted retries: send to DLQ
                            await self._send_to_dlq(activity, context, result)
                            raise Exception(f"Activity {activity['name']} failed after retries: {result.error_message}")
                    elif error_action == "stop":
                        # send to DLQ
                        await self._send_to_dlq(activity, context, result)
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

            # Send execution completed event via WebSocket
            await websocket_manager.send_execution_completed(
                execution_id=execution_id,
                tenant_id=tenant_id,
                status="COMPLETED",
                execution_time_ms=execution_time_ms,
                activities_executed=len([log for log in context.execution_log if log["status"] != "skipped"]),
                activities_skipped=len([log for log in context.execution_log if log["status"] == "skipped"])
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
                            "code_output", "execution_result", "script_output", "hl7_messages",
                            "icare_response", "icare_results"
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

            execution_time_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)

            # Update execution record with failure
            await update_execution_record(
                execution_id,
                status="FAILED",
                completed_at=datetime.utcnow(),
                execution_time_ms=execution_time_ms,
                error_message=str(e),
                execution_log=context.execution_log if 'context' in locals() else []
            )

            # Send execution failed event via WebSocket
            if 'context' in locals():
                await websocket_manager.send_execution_completed(
                    execution_id=execution_id,
                    tenant_id=tenant_id,
                    status="FAILED",
                    execution_time_ms=execution_time_ms,
                    activities_executed=len([log for log in context.execution_log if log["status"] != "skipped"]),
                    activities_skipped=len([log for log in context.execution_log if log["status"] == "skipped"]),
                    error_message=str(e)
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

            result = await processor(activity, context)


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

    def _log_activity_skip(self, context: WorkflowContext, activity: Dict[str, Any], reason: str = "Activity is disabled"):
        """Log skipped activity"""
        context.execution_log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "activity": activity["name"],
            "activity_type": activity.get("activity_type"),
            "status": "skipped",
            "reason": reason
        })

    async def _send_to_dlq(self, activity: Dict[str, Any], context: WorkflowContext, result: ActivityResult) -> None:
        try:
            from models.dlq import DLQRepository
            import uuid as _uuid
            wf_uuid = _uuid.UUID(str(context.workflow_id)) if isinstance(context.workflow_id, str) else context.workflow_id
            ex_uuid = _uuid.UUID(str(context.execution_id)) if isinstance(context.execution_id, str) else None
            act_id = activity.get('id')
            act_uuid = _uuid.UUID(str(act_id)) if act_id else None
            payload = {
                'variables': context.variables,
                'activity_config': activity.get('config')
            }
            error_handling = activity.get('error_handling', {}) or {}
            max_retries = int(error_handling.get('retry_count', 0) or 0)
            await DLQRepository.add(
                tenant_id=_uuid.UUID(str(context.tenant_id)),
                workflow_id=wf_uuid,
                execution_id=ex_uuid,
                activity_id=act_uuid,
                activity_name=activity.get('name'),
                error_message=result.error_message,
                payload=payload,
                max_retries=max_retries,
                next_attempt_at=None,
            )
        except Exception as e:
            logger.error(f"Failed to record DLQ entry: {e}")


# Global instance
workflow_execution_service = WorkflowExecutionService()
