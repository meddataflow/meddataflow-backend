"""
HL7-specific activity processors
Extracted from workflow_execution_service.py for better code organization
"""
import csv
import io
import uuid
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus
from services.hl7_parser import HL7Parser, ParsedHL7Message
from services.hl7_mapper_service import hl7_mapper_service
from services.generic_hl7_mapper import generic_hl7_mapper

logger = logging.getLogger(__name__)

# Initialize HL7 parser instance
hl7_parser = HL7Parser()


async def process_hl7_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Process HL7 Parser activity - Parse HL7 message and show in English readable format
    Store variables as global variables for use in other activities
    """
    config = activity.get("config", {})
    variable_definitions = config.get("variables", [])
    readable_format = config.get("readable_format", True)

    if not context.raw_message:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No HL7 message available to parse"
        )

    try:
        # Parse HL7 message
        parsed_message = hl7_parser.parse_message(context.raw_message)
        context.message = parsed_message

        # Extract variables based on configuration
        extracted_vars = {}
        for var_def in variable_definitions:
            var_name = var_def.get("name")
            var_source = var_def.get("source")  # e.g., "PID.5.1" for patient first name
            var_default = var_def.get("default", "")

            if var_name and var_source:
                # Special handling for MESSAGE_TYPE - extract from parsed message metadata
                if var_name == "MESSAGE_TYPE" and parsed_message:
                    parsed_dict = parsed_message.to_dict()
                    message_type = parsed_dict.get("message_type", "")
                    extracted_vars[var_name] = message_type
                    context.variables[var_name] = message_type
                else:
                    value = _extract_hl7_field_value(context.raw_message, var_source, var_default)
                    # PHI-safe logging - only log field path and success status
                    extracted_vars[var_name] = value
                    context.variables[var_name] = value

        # Generate readable format
        readable_text = ""
        if readable_format:
            readable_text = _generate_readable_hl7_text(parsed_message)

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "HL7 message parsed successfully",
                "parsed_message": parsed_message.to_dict() if parsed_message else {},
                "raw_message": context.raw_message,  # Include raw message for timeline display
                "readable_text": readable_text,
                "extracted_variables": extracted_vars
            },
            variables=extracted_vars
        )

    except Exception as e:
        # PHI-safe error logging - don't include message content
        logger.error(f"Error parsing HL7 message: {type(e).__name__}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to parse HL7 message: {type(e).__name__}"
        )


async def process_hl7_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Process HL7 Transformer activity - Transform HL7 to another HL7 format
    Example: Move MSH.3 to ZPF.1 or hardcode strings in segments
    """
    config = activity.get("config", {})
    transformation_mappings = config.get("mappings", [])

    if not context.raw_message:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No HL7 message available to transform"
        )

    try:
        # Store original message before transformation
        original_message = context.raw_message

        # Use hl7_mapper_service for transformation
        transform_config = {
            "mappings": transformation_mappings
        }

        transformed_message = hl7_mapper_service.create_hl7_to_hl7_mapping(
            context.raw_message,
            transform_config
        )

        if transformed_message:
            # Update context with transformed message
            context.raw_message = transformed_message
            context.message = hl7_parser.parse_message(transformed_message)

            return ActivityResult(
                status=ActivityStatus.COMPLETED,
                output_data={
                    "message": "HL7 message transformed successfully",
                    "transformed_message": transformed_message,  # Include transformed message for API response
                    "raw_message": transformed_message,  # Include transformed message for timeline display
                    "original_message": original_message,  # Keep original for comparison
                    "transformation_mappings": transformation_mappings
                }
            )
        else:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="HL7 transformation failed"
            )

    except Exception as e:
        # PHI-safe error logging - don't include message content
        logger.error(f"Error transforming HL7 message: {type(e).__name__}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to transform HL7 message: {type(e).__name__}"
        )


async def process_hl7_to_fhir_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Process HL7 to FHIR conversion activity
    Convert any HL7 message to FHIR format
    """
    config = activity.get("config", {})
    fhir_resource_type = config.get("resource_type", "Patient")
    mapping_config = config.get("mappings", {})

    if not context.raw_message:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No HL7 message available to convert to FHIR"
        )

    try:
        # Extract segments for FHIR mapping
        segments = hl7_mapper_service.parse_hl7_segments(context.raw_message)

        # Extract common HL7 fields directly from segments
        patient_id = ""
        patient_family = ""
        patient_given = ""
        patient_dob = ""
        patient_gender = ""
        encounter_class = ""

        if "PID" in segments and segments["PID"]:
            pid_segment = segments["PID"][0]
            patient_id = hl7_mapper_service.extract_segment_field(pid_segment, 3)  # PID.3
            patient_name = hl7_mapper_service.extract_segment_field(pid_segment, 5)  # PID.5
            if patient_name:
                name_parts = patient_name.split("^")
                if len(name_parts) >= 2:
                    patient_family = name_parts[0]
                    patient_given = name_parts[1]
            patient_dob = hl7_mapper_service.extract_segment_field(pid_segment, 7)  # PID.7
            patient_gender = hl7_mapper_service.extract_segment_field(pid_segment, 8)  # PID.8

        if "PV1" in segments and segments["PV1"]:
            pv1_segment = segments["PV1"][0]
            encounter_class = hl7_mapper_service.extract_segment_field(pv1_segment, 2)  # PV1.2

        # Create FHIR Bundle with Patient resource
        resources = []

        # Patient resource
        patient_resource = {
            "resourceType": "Patient",
            "id": patient_id or f"patient-{uuid.uuid4()}",
            "meta": {
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            },
            "identifier": [{
                "system": "http://hospital.example.org/patients",
                "value": patient_id
            }] if patient_id else [],
            "name": [{
                "family": patient_family,
                "given": [patient_given] if patient_given else []
            }] if patient_family or patient_given else [],
            "gender": _map_gender(patient_gender),
            "birthDate": _format_date(patient_dob)
        }

        # Clean empty fields
        patient_resource = {k: v for k, v in patient_resource.items() if v}
        resources.append(patient_resource)

        # Encounter resource if PV1 exists
        if encounter_class:
            encounter_resource = {
                "resourceType": "Encounter",
                "id": f"encounter-{uuid.uuid4()}",
                "meta": {
                    "lastUpdated": datetime.utcnow().isoformat() + "Z"
                },
                "status": "in-progress",
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": encounter_class.lower(),
                    "display": encounter_class
                },
                "subject": {
                    "reference": f"Patient/{patient_resource['id']}"
                }
            }
            resources.append(encounter_resource)

        # Create FHIR Bundle
        fhir_bundle = {
            "resourceType": "Bundle",
            "id": f"bundle-{uuid.uuid4()}",
            "meta": {
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            },
            "type": "message",
            "entry": [
                {
                    "resource": resource,
                    "fullUrl": f"urn:uuid:{resource['id']}"
                }
                for resource in resources
            ]
        }

        # Apply custom mappings if specified
        for fhir_field, mapping in mapping_config.items():
            source_segment = mapping.get("segment")
            source_field = mapping.get("field")
            transform = mapping.get("transform", "direct")

            if source_segment in segments and segments[source_segment]:
                segment = segments[source_segment][0]  # Use first occurrence
                value = hl7_mapper_service.extract_segment_field(segment, source_field)

                # Apply transformation
                if transform == "uppercase":
                    value = value.upper()
                elif transform == "lowercase":
                    value = value.lower()
                elif transform == "gender_mapping":
                    value = {"M": "male", "F": "female"}.get(value, "unknown")

                # Apply to appropriate resource in bundle
                if fhir_bundle["entry"] and "resource" in fhir_bundle["entry"][0]:
                    fhir_bundle["entry"][0]["resource"][fhir_field] = value

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "HL7 to FHIR conversion completed",
                "fhir_bundle": fhir_bundle,
                "fhir_resource": patient_resource,  # Keep for backward compatibility
                "resource_count": len(resources)
            }
        )

    except Exception as e:
        logger.error(f"Error converting HL7 to FHIR: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to convert HL7 to FHIR: {str(e)}"
        )


async def process_hl7_to_csv_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Process HL7 to CSV conversion activity
    User creates custom headers and decides what segment to put in each header
    Can use global variables defined in parser activity or hardcode strings
    """
    config = activity.get("config", {})
    headers = config.get("headers", [])
    mappings = config.get("mappings", {})
    # Support UI shape: csv_headers (array or CSV string) and field_mappings (JSON string)
    csv_headers = config.get("csv_headers")
    field_mappings = config.get("field_mappings")
    if csv_headers:
        if isinstance(csv_headers, str):
            headers = [h.strip() for h in csv_headers.split(',') if h.strip()]
        elif isinstance(csv_headers, list):
            headers = csv_headers
    if field_mappings and isinstance(field_mappings, str):
        try:
            import json as _json
            parsed = _json.loads(field_mappings)
            if isinstance(parsed, dict):
                for col, path in parsed.items():
                    mappings[col] = {"source_location": str(path)}
        except Exception:
            pass

    if not context.raw_message:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No HL7 message available to convert to CSV"
        )

    try:
        csv_data = []
        csv_row = {}

        # Process each header
        for header in headers:
            mapping_info = mappings.get(header, {})

            # Determine source_location
            source_location = None
            if isinstance(mapping_info, str):
                source_location = mapping_info
            elif isinstance(mapping_info, dict) and "source_location" in mapping_info:
                source_location = mapping_info["source_location"]

            if source_location:
                try:
                    # Handle variable references (variable:variable_name)
                    if source_location.startswith("variable:"):
                        variable_name = source_location.replace("variable:", "")
                        csv_row[header] = context.variables.get(variable_name, mapping_info.get("default_value", ""))
                    else:
                        # Parse source_location format like "PID.3" or "PID.5.1"
                        parts = source_location.split(".")
                        if len(parts) >= 2:
                            segment_name = parts[0]
                            field_number = int(parts[1])
                            component = int(parts[2]) - 1 if len(parts) > 2 else 0  # Convert to 0-based

                            segments = hl7_mapper_service.parse_hl7_segments(context.raw_message)
                            if segment_name in segments and segments[segment_name]:
                                segment = segments[segment_name][0]  # Use first occurrence
                                csv_row[header] = hl7_mapper_service.extract_segment_field(
                                    segment, field_number, component
                                )
                            else:
                                csv_row[header] = mapping_info.get("default_value", "")
                        else:
                            csv_row[header] = mapping_info.get("default_value", "")
                except (ValueError, IndexError):
                    csv_row[header] = mapping_info.get("default_value", "")
            else:
                # Legacy format support
                value_type = mapping_info.get("type", "segment")  # segment, variable, hardcode

                if value_type == "variable":
                    # Use global variable
                    variable_name = mapping_info.get("variable")
                    csv_row[header] = context.variables.get(variable_name, "")

                elif value_type == "hardcode":
                    # Use hardcoded string
                    csv_row[header] = mapping_info.get("value", "")

                elif value_type == "segment":
                    # Extract from HL7 segment
                    segment_name = mapping_info.get("segment")
                    field_number = mapping_info.get("field", 1)
                    component = mapping_info.get("component", 0)

                    segments = hl7_mapper_service.parse_hl7_segments(context.raw_message)
                    if segment_name in segments and segments[segment_name]:
                        segment = segments[segment_name][0]  # Use first occurrence
                        csv_row[header] = hl7_mapper_service.extract_segment_field(
                            segment, field_number, component
                        )
                    else:
                        csv_row[header] = ""
                else:
                    csv_row[header] = ""

        csv_data.append(csv_row)

        # Convert to CSV string
        csv_string = _convert_to_csv_string(csv_data, headers)

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "HL7 to CSV conversion completed",
                "csv_data": csv_data,
                "csv_string": csv_string,
                "headers": headers
            },
            variables={
                "csv_row": csv_row,
                "csv_headers": headers,
                "csv_string": csv_string
            }
        )

    except Exception as e:
        logger.error(f"Error converting HL7 to CSV: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to convert HL7 to CSV: {str(e)}"
        )


async def process_segment_loop_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Process Segment Loop activity - Loop through segments like EVN1, EVN2, EVN3 or HL7 fields
    Also supports hl7_target format for field-level looping (e.g., OBX.5 for observation values)
    """
    config = activity.get("config", {})
    # Accept frontend alias 'segment_type'
    segment_name = config.get("segment_name") or config.get("segment_type") or "EVN"
    # Accept frontend 'nested_activities' (JSON string or list) as loop_activities
    loop_activities = config.get("loop_activities", [])
    nested = config.get("nested_activities")
    if nested and not loop_activities:
        try:
            if isinstance(nested, str):
                import json as _json
                parsed = _json.loads(nested)
                if isinstance(parsed, list):
                    loop_activities = parsed
            elif isinstance(nested, list):
                loop_activities = nested
        except Exception:
            pass

    # Support new format compatible with loop activity
    # Normalize loop mode from frontend options
    loop_mode = config.get("mode") or (
        'each-segment' if (config.get('loop_mode') in ('numbered_segments', 'all_occurrences', None)) else 'each-segment'
    )
    hl7_target = config.get("hl7_target", segment_name)
    variable_name = config.get("variable_name", "loop_item")
    index_variable = config.get("index_variable", "loop_index")
    max_iterations = config.get("max_iterations", 100)

    if not context.raw_message:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No HL7 message available for segment looping"
        )

    try:
        segments = hl7_mapper_service.parse_hl7_segments(context.raw_message)
        loop_results = []
        iterations = 0

        # Handle different loop modes
        if loop_mode == "each-hl7-item" and "." in hl7_target:
            # Field-level iteration (e.g., "OBX.5" for all OBX observation values)
            segment_type, field_num = hl7_target.split(".", 1)
            field_number = int(field_num) if field_num.isdigit() else 1

            if segment_type in segments:
                for i, segment in enumerate(segments[segment_type][:max_iterations]):
                    iterations += 1
                    field_value = hl7_mapper_service.extract_segment_field(segment, field_number)

                    context.variables[variable_name] = field_value
                    context.variables[index_variable] = i + 1

                    loop_results.append({
                        "iteration": i + 1,
                        "segment_type": segment_type,
                        "field_number": field_number,
                        "field_value": field_value,
                        "status": "processed"
                    })

                    # Execute actions within loop if specified
                    actions = config.get("actions", [])
                    for action in actions:
                        action_type = action.get("type")
                        if action_type == "set_variable":
                            var_name = action.get("variable")
                            var_value = action.get("value", "")

                            # Support variable substitution
                            for key, value in context.variables.items():
                                var_value = str(var_value).replace(f"{{{{{key}}}}}", str(value))

                            context.variables[var_name] = var_value

                return ActivityResult(
                    status=ActivityStatus.COMPLETED,
                    output_data={
                        "message": f"Successfully looped through {iterations} {hl7_target} field values",
                        "hl7_target": hl7_target,
                        "segment_count": iterations,
                        "loop_results": loop_results,
                        "iterations_completed": iterations
                    },
                    variables=context.variables
                )
        else:
            # Legacy segment-level iteration
            target_segments = segments.get(segment_name, [])

            if not target_segments:
                return ActivityResult(
                    status=ActivityStatus.COMPLETED,
                    output_data={
                        "message": f"No {segment_name} segments found to loop through",
                        "segment_count": 0,
                        "loop_results": []
                    }
                )

        loop_results = []

        # Loop through each segment instance
        for i, segment in enumerate(target_segments):
            loop_context = WorkflowContext(
                workflow_id=context.workflow_id,
                execution_id=context.execution_id,
                tenant_id=context.tenant_id,
                variables=context.variables.copy(),
                message=context.message,
                raw_message=context.raw_message,  # Keep original complete message
                current_activity=f"{segment_name}_loop_{i+1}"
            )

            # Add loop-specific variables
            loop_context.variables[f"{segment_name}_INDEX"] = i + 1
            loop_context.variables[f"{segment_name}_SEGMENT"] = segment

            segment_results = []

            # Execute loop activities for this segment
            for loop_activity in loop_activities:
                activity_type = loop_activity.get("activity_type", "").lower()
                # Note: This would need to be implemented with proper activity processor registry
                # For now, we'll create placeholder results
                segment_results.append({
                    "activity_type": activity_type,
                    "status": "completed",
                    "output": {"message": f"Processed {activity_type} for segment {i+1}"}
                })

            loop_results.append({
                "segment_index": i + 1,
                "segment_content": segment,
                "activity_results": segment_results
            })

            # Update main context with any new variables from loop
            context.variables.update(loop_context.variables)

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": f"Successfully looped through {len(target_segments)} {segment_name} segments",
                "segment_name": segment_name,
                "segment_count": len(target_segments),
                "loop_results": loop_results
            }
        )

    except Exception as e:
        logger.error(f"Error in segment loop activity: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to process segment loop: {str(e)}"
        )


# Helper functions for HL7 activities

def _extract_hl7_field_value(hl7_message: str, field_path: str, default: str = "") -> str:
    """
    Extract value from HL7 message using field path

    Supports multiple formats:
    - Standard dot notation: PID.5.1 (segment.field.component)
    - Named fields: Patient.Name.Last (semantic field names)
    - Extended notation: PID.5.1.2 (with subcomponents)

    Uses generic HL7 mapper for version-agnostic extraction
    """
    # Support special 'raw' path to return the entire HL7 message
    if field_path and field_path.lower() == 'raw':
        return hl7_message or default

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
            component = int(parts[2]) if len(parts) > 2 else 0

            if segment_name in segments and segments[segment_name]:
                segment = segments[segment_name][0]  # Use first occurrence
                return hl7_mapper_service.extract_segment_field(segment, field_number, component)

        except Exception as fallback_error:
            logger.warning(f"Fallback HL7 field extraction also failed {field_path}: {fallback_error}")

    return default


def _generate_readable_hl7_text(parsed_message: ParsedHL7Message) -> str:
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


def _convert_to_csv_string(data: List[Dict], headers: List[str]) -> str:
    """Convert list of dictionaries to CSV string"""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    return output.getvalue()


def _map_gender(hl7_gender: str) -> str:
    """Map HL7 gender codes to FHIR gender codes"""
    mapping = {
        "M": "male",
        "F": "female",
        "O": "other",
        "U": "unknown"
    }
    return mapping.get(hl7_gender.upper(), "unknown")


def _format_date(hl7_date: str) -> str:
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
