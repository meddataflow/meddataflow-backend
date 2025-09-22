"""
Core Activity Processors
Extracted from workflow_execution_service.py for the main goal workflow pattern:
Receiver → Filter → Transform → CSV → S3
"""
import csv
import io
import os
import uuid
import logging
from typing import Dict, Any
from datetime import datetime

from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus
from services.s3_service import s3_service
from .gcs_storage_processor import process_gcs_storage_activity

logger = logging.getLogger(__name__)


async def process_filter_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Process Filter activity - Check conditions
    Example: Check if SENDING_APPLICATION equals "EMR1"
    """
    config = activity.get("config", {})
    conditions = config.get("conditions", [])
    logical_operator = config.get("logical_operator", "AND")

    if not conditions:
        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={"filter_passed": True, "reason": "No conditions specified"}
        )

    # Evaluate each condition
    condition_results = []
    for condition in conditions:
        variable = condition.get("variable")
        operator = condition.get("operator", "equals")
        expected_value = condition.get("value")

        actual_value = context.variables.get(variable)

        # Evaluate condition
        result = _evaluate_condition(actual_value, operator, expected_value)
        condition_results.append(result)


    # Apply logical operator
    if logical_operator == "AND":
        filter_passed = all(condition_results)
    else:  # OR
        filter_passed = any(condition_results)

    if not filter_passed:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Filter conditions not met ({logical_operator})",
            output_data={"filter_passed": False, "conditions_evaluated": len(conditions)}
        )

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={
            "filter_passed": True,
            "conditions_evaluated": len(conditions),
            "logical_operator": logical_operator
        }
    )


async def process_transform_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Process Transform activity - Transform message to another format
    Example: Transform HL7 to FHIR
    """
    config = activity.get("config", {})
    input_format = config.get("input_format", "HL7v2")
    output_format = config.get("output_format", "FHIR")
    mappings = config.get("mappings", [])

    transformed_data = {}

    if output_format == "FHIR":
        # Simple FHIR transformation example
        transformed_data = {
            "resourceType": "Patient",
            "id": context.variables.get("PATIENT_ID", "unknown"),
            "identifier": [{
                "system": "http://hospital.example.org/patients",
                "value": context.variables.get("PATIENT_ID", "")
            }],
            "name": [{
                "family": context.variables.get("PATIENT_LAST_NAME", ""),
                "given": [context.variables.get("PATIENT_FIRST_NAME", "")]
            }],
            "gender": _map_gender(context.variables.get("PATIENT_GENDER", "")),
            "birthDate": _format_date(context.variables.get("PATIENT_DOB", ""))
        }
    elif output_format == "JSON":
        # Apply custom mappings
        for mapping in mappings:
            source = mapping.get("source")
            target = mapping.get("target")
            transform = mapping.get("transform", "direct")

            value = context.variables.get(source, "")
            if transform == "uppercase":
                value = str(value).upper()
            elif transform == "lowercase":
                value = str(value).lower()

            transformed_data[target] = value
    else:
        # Default: pass through variables
        transformed_data = context.variables.copy()

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={
            "input_format": input_format,
            "output_format": output_format,
            "transformed_data": transformed_data
        },
        variables={"transformed_data": transformed_data}
    )


async def process_csv_converter_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Process CSV Converter activity - Convert data to CSV format
    """
    config = activity.get("config", {})
    fields = config.get("fields", [])
    delimiter = config.get("delimiter", ",")
    include_headers = config.get("include_headers", True)

    # Prepare CSV data
    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer, delimiter=delimiter)

    # Write headers if requested
    if include_headers:
        headers = [field.get("name", "") for field in fields]
        csv_writer.writerow(headers)

    # Write data row
    row_data = []
    for field in fields:
        source = field.get("source")
        default = field.get("default", "")

        # Get value from variables or use default
        value = context.variables.get(source, default)
        row_data.append(str(value))

    csv_writer.writerow(row_data)

    csv_content = csv_buffer.getvalue()
    csv_buffer.close()

    # Generate filename
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    message_id = context.variables.get("MESSAGE_CONTROL_ID", uuid.uuid4().hex[:8])
    filename = f"hl7_data_{message_id}_{timestamp}.csv"

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={
            "csv_content": csv_content,
            "filename": filename,
            "row_count": 1,
            "field_count": len(fields)
        },
        variables={
            "csv_content": csv_content,
            "csv_filename": filename
        }
    )


async def process_s3_storage_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Process S3 Storage activity - Store CSV file in S3 bucket
    """
    config = activity.get("config", {})

    # Handle both direct bucket name and nested bucket config
    bucket_config = config.get("bucket", {})
    if isinstance(bucket_config, dict):
        bucket = bucket_config.get("name", "hl7-processed-files")
        key_pattern = bucket_config.get("key_prefix", "{tenant_id}/{date}/") + bucket_config.get("file_pattern", "{filename}")
    else:
        bucket = bucket_config if bucket_config else "hl7-processed-files"
        key_pattern = config.get("key_pattern", "{tenant_id}/{date}/{filename}")

    encryption = config.get("encryption", True)

    # Get content to store - try multiple sources
    csv_content = context.variables.get("csv_content")
    csv_filename = context.variables.get("csv_filename", f"data_{uuid.uuid4().hex}.csv")

    # If no CSV content, try to create JSON content from available variables
    if not csv_content:
        # Create JSON content from available variables (excluding system variables)
        system_vars = {"source", "metadata", "trigger_type", "message"}
        data_vars = {k: v for k, v in context.variables.items()
                    if not k.startswith("http_") and not k.startswith("file_")
                    and not k.startswith("email_") and not k.startswith("db_")
                    and k not in system_vars}

        if data_vars:
            import json
            from datetime import datetime

            # Custom JSON encoder to handle UUID and other non-serializable objects
            def json_serializer(obj):
                if hasattr(obj, 'hex'):  # UUID objects
                    return str(obj)
                elif isinstance(obj, datetime):
                    return obj.isoformat()
                else:
                    return str(obj)

            csv_content = json.dumps(data_vars, indent=2, default=json_serializer)
            csv_filename = f"workflow_data_{uuid.uuid4().hex[:8]}.json"
        else:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="No content available to store (no csv_content or extractable data)"
            )

    # Generate S3 key
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    timestamp_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    # Handle both single and double curly brace patterns
    s3_key = key_pattern
    substitutions = {
        "tenant_id": context.tenant_id,
        "date": date_str,
        "filename": csv_filename,
        "message_id": context.variables.get("MESSAGE_CONTROL_ID", "unknown"),
        "timestamp": timestamp_str,
        **context.variables  # Include all variables for substitution
    }

    # Replace both {{var}} and {var} patterns
    for var_name, var_value in substitutions.items():
        double_placeholder = f"{{{{{var_name}}}}}"
        single_placeholder = f"{{{var_name}}}"
        s3_key = s3_key.replace(double_placeholder, str(var_value))
        s3_key = s3_key.replace(single_placeholder, str(var_value))

    try:
        # Upload to S3
        result = await s3_service.upload_content(
            bucket=bucket,
            key=s3_key,
            content=csv_content,
            content_type="text/csv",
            metadata={
                "workflow_id": str(context.workflow_id),
                "execution_id": str(context.execution_id),
                "tenant_id": str(context.tenant_id),
                "message_type": context.variables.get("MESSAGE_TYPE", ""),
                "processed_at": datetime.utcnow().isoformat()
            }
        )

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "s3_bucket": bucket,
                "s3_key": s3_key,
                "s3_url": f"s3://{bucket}/{s3_key}",
                "file_size": len(csv_content.encode()),
                "upload_timestamp": datetime.utcnow().isoformat()
            },
            variables={
                "s3_bucket": bucket,
                "s3_key": s3_key,
                "s3_url": f"s3://{bucket}/{s3_key}"
            }
        )

    except Exception as e:
        logger.error(f"Failed to upload to S3: {e}")

        # Fallback: Store locally
        import os
        local_dir = f"/tmp/hl7_files/{context.tenant_id}/{date_str}"
        os.makedirs(local_dir, exist_ok=True)

        local_path = os.path.join(local_dir, csv_filename)
        with open(local_path, 'w') as f:
            f.write(csv_content)

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "storage_type": "local",
                "local_path": local_path,
                "file_size": len(csv_content.encode()),
                "note": f"S3 upload failed, stored locally: {e}"
            },
            variables={
                "storage_type": "local",
                "local_path": local_path
            }
        )


# ==================== HELPER FUNCTIONS ====================

def _evaluate_condition(actual_value: Any, operator: str, expected_value: Any) -> bool:
    """Evaluate a condition"""
    actual_str = str(actual_value) if actual_value is not None else ""
    expected_str = str(expected_value) if expected_value is not None else ""

    if operator == "equals":
        return actual_str == expected_str
    elif operator == "not_equals":
        return actual_str != expected_str
    elif operator == "contains":
        return expected_str in actual_str
    elif operator == "starts_with":
        return actual_str.startswith(expected_str)
    elif operator == "ends_with":
        return actual_str.endswith(expected_str)
    elif operator == "greater_than":
        try:
            return float(actual_str) > float(expected_str)
        except:
            return False
    elif operator == "less_than":
        try:
            return float(actual_str) < float(expected_str)
        except:
            return False
    elif operator == "is_empty":
        return not actual_str
    elif operator == "is_not_empty":
        return bool(actual_str)
    else:
        return False


def _map_gender(hl7_gender: str) -> str:
    """Map HL7 gender to FHIR gender"""
    mapping = {
        "M": "male",
        "F": "female",
        "O": "other",
        "U": "unknown"
    }
    return mapping.get(hl7_gender.upper(), "unknown")


def _format_date(hl7_date: str) -> str:
    """Format HL7 date to ISO format"""
    if not hl7_date or len(hl7_date) < 8:
        return ""

    try:
        year = hl7_date[0:4]
        month = hl7_date[4:6]
        day = hl7_date[6:8]
        return f"{year}-{month}-{day}"
    except:
        return hl7_date