"""
Workflow Utility Functions
Extracted from workflow_execution_service.py - Contains helper methods for workflow execution
"""

import csv
import io
import logging
from typing import Any, Dict, List
from datetime import datetime

from services.hl7_parser import ParsedHL7Message
from services.hl7_mapper_service import hl7_mapper_service
from services.generic_hl7_mapper import generic_hl7_mapper

logger = logging.getLogger(__name__)


def extract_hl7_value(message: ParsedHL7Message, path: str) -> str:
    """
    Extract value from HL7 message using path notation
    Examples: MSH.3, PID.5.1, OBX[0].5
    """
    try:
        parts = path.split('.')
        segment_name = parts[0]

        # Find segment
        segment = None
        for seg in message.segments:
            if seg.type == segment_name:
                segment = seg
                break

        if not segment:
            return ""

        # Navigate to field
        if len(parts) > 1:
            field_index = int(parts[1]) - 1  # HL7 uses 1-based indexing
            if field_index < len(segment.fields):
                field = segment.fields[field_index]

                # Handle component if specified
                if len(parts) > 2 and '^' in field.value:
                    components = field.value.split('^')
                    component_index = int(parts[2]) - 1
                    if component_index < len(components):
                        return components[component_index]

                return field.value

        return ""

    except Exception as e:
        logger.warning(f"Failed to extract HL7 value from path {path}: {e}")
        return ""


def evaluate_condition(actual_value: Any, operator: str, expected_value: Any) -> bool:
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


def map_gender(hl7_gender: str) -> str:
    """Map HL7 gender codes to FHIR gender codes"""
    mapping = {
        "M": "male",
        "F": "female",
        "O": "other",
        "U": "unknown"
    }
    return mapping.get(hl7_gender.upper(), "unknown")


def format_date(hl7_date: str) -> str:
    """Format HL7 date to FHIR date format"""
    if not hl7_date:
        return ""

    # HL7 date format is typically YYYYMMDD
    if len(hl7_date) >= 8:
        year = hl7_date[:4]
        month = hl7_date[4:6]
        day = hl7_date[6:8]
        return f"{year}-{month}-{day}"

    return hl7_date


def extract_hl7_field_value(hl7_message: str, field_path: str, default: str = "") -> str:
    """
    Extract value from HL7 message using field path

    Supports multiple formats:
    - Standard dot notation: PID.5.1 (segment.field.component)
    - Named fields: Patient.Name.Last (semantic field names)
    - Extended notation: PID.5.1.2 (with subcomponents)

    Uses generic HL7 mapper for version-agnostic extraction
    """
    try:
        # Use generic mapper for version-agnostic extraction
        return generic_hl7_mapper.extract_field_generic(hl7_message, field_path, default)

    except Exception as e:
        logger.warning(f"Error extracting HL7 field {field_path} with generic mapper: {e}")

        # Fallback to legacy mapper for backward compatibility
        try:
            segments = hl7_mapper_service.parse_hl7_segments(hl7_message)

            # Parse field path (e.g., "PID.5.1")
            parts = field_path.split('.')
            segment_name = parts[0]
            field_number = int(parts[1]) if len(parts) > 1 else 1
            # Convert HL7 1-based component to 0-based; if not provided, keep None to return full field
            component = (int(parts[2]) - 1) if len(parts) > 2 else None

            if segment_name in segments and segments[segment_name]:
                segment = segments[segment_name][0]  # Use first occurrence
                return hl7_mapper_service.extract_segment_field(segment, field_number, component)

        except Exception as fallback_error:
            logger.warning(f"Fallback HL7 field extraction also failed {field_path}: {fallback_error}")

    return default


def generate_readable_hl7_text(parsed_message: ParsedHL7Message) -> str:
    """Generate human-readable text from parsed HL7 message"""
    if not parsed_message:
        return ""

    readable_parts = []
    message_dict = parsed_message.to_dict()

    # Extract common fields for readable format
    patient_name = message_dict.get("patient_name", "Unknown Patient")
    message_type = message_dict.get("message_type", "Unknown")
    facility = message_dict.get("sending_facility", "Unknown Facility")

    readable_parts.append(f"Message Type: {message_type}")
    readable_parts.append(f"Patient: {patient_name}")
    readable_parts.append(f"From: {facility}")

    # Add timestamp if available
    timestamp = message_dict.get("timestamp")
    if timestamp:
        readable_parts.append(f"Timestamp: {timestamp}")

    return " | ".join(readable_parts)


def convert_to_csv_string(data: List[Dict], headers: List[str]) -> str:
    """Convert list of dictionaries to CSV string"""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    return output.getvalue()


def execute_condition_action(action: str, action_config: Dict[str, Any], context) -> str:
    """Execute the action specified by a condition"""
    if action == "set_variable":
        variable = action_config.get("variable")
        value = action_config.get("value")
        if variable and value is not None:
            context.variables[variable] = value
            return f"Set {variable} = {value}"
    elif action == "set_path":
        path = action_config.get("path")
        if path:
            context.variables["selected_path"] = path
            return f"Selected path: {path}"
    return f"Executed action: {action}"


def execute_frontend_condition_action(action: str, context) -> str:
    """Execute frontend-style condition actions (on_true/on_false)"""
    if action == "continue":
        return "Continue to next activity"
    elif action == "skip":
        context.variables["skip_next_activity"] = True
        return "Skip next activity"
    elif action == "jump":
        context.variables["jump_to_activity"] = True
        return "Jump to specific activity"
    elif action == "stop":
        context.variables["stop_workflow"] = True
        return "Stop workflow"
    return f"Executed frontend action: {action}"


def log_activity_execution(context, activity: Dict[str, Any], result) -> None:
    """Log activity execution to context"""
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "activity": activity["name"],
        "activity_type": activity.get("activity_type"),
        "status": result.status.value,
        "execution_time_ms": result.execution_time_ms,
        "output_data": result.output_data
    }

    if result.error_message:
        log_entry["error"] = result.error_message

    context.execution_log.append(log_entry)


def log_activity_skip(context, activity: Dict[str, Any]) -> None:
    """Log skipped activity"""
    context.execution_log.append({
        "timestamp": datetime.utcnow().isoformat(),
        "activity": activity["name"],
        "activity_type": activity.get("activity_type"),
        "status": "skipped",
        "reason": "Activity is disabled"
    })
