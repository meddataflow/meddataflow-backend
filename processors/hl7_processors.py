"""
HL7-specific activity processors
Extracted from workflow_execution_service.py for better code organization
"""
import csv
import io
import uuid
import base64
import logging
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from typing import Tuple
import os

from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus
from services.hl7_parser import HL7Parser, ParsedHL7Message
from services.hl7_ack import generate_ack
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

        # Normalize mappings to internal list format
        mappings_list = []
        if isinstance(transformation_mappings, dict):
            # Support shorthand mapping: { "ZPX.1.1": "SCH.6.1" } or { "ZPX.1": {"value": "OFFICE"} }
            for target_path, spec in transformation_mappings.items():
                if isinstance(spec, dict):
                    mapping_item = {"target": target_path}
                    if "source" in spec:
                        mapping_item["source"] = spec["source"]
                    if "value" in spec:
                        mapping_item["value"] = spec["value"]
                    if "transform" in spec:
                        mapping_item["transform"] = spec["transform"]
                    mappings_list.append(mapping_item)
                elif isinstance(spec, str):
                    # If spec looks like a field path (has a dot), treat as source; otherwise as a literal value
                    if "." in spec:
                        mappings_list.append({"target": target_path, "source": spec})
                    else:
                        mappings_list.append({"target": target_path, "value": spec})
        elif isinstance(transformation_mappings, list):
            mappings_list = transformation_mappings
        else:
            mappings_list = []

        # Resolve template variables in hardcoded values like "{{var}}"
        import re
        var_pattern = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

        def _interpolate(value: str) -> str:
            if not isinstance(value, str):
                return value
            def _repl(m):
                key = m.group(1)
                return str(context.variables.get(key, ""))
            return var_pattern.sub(_repl, value)

        for m in mappings_list:
            if isinstance(m, dict) and 'value' in m:
                m['value'] = _interpolate(m.get('value', ''))

        # Use hl7_mapper_service for transformation
        transform_config = {"mappings": mappings_list}

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
    Convert any HL7 message to FHIR format - Generic converter for all HL7 message types
    """
    config = activity.get("config", {})
    mapping_config = config.get("mappings", {})

    # Get the original message from variables (before any transformations)
    original_message = context.variables.get("message", context.raw_message)

    if not original_message:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No HL7 message available to convert to FHIR"
        )

    try:
        # Extract segments for FHIR mapping using the original message
        logger.info(f"FHIR converter using original message (first 200 chars): {original_message[:200] if original_message else 'None'}")
        # Ensure proper line endings for parsing
        normalized_message = original_message.replace('\\n', '\n').replace('\\r', '\r')
        segments = hl7_mapper_service.parse_hl7_segments(normalized_message)
        logger.info(f"After normalization, found segments: {list(segments.keys())}")

        # Get message type from MSH segment to determine FHIR bundle type
        message_type = "message"
        if "MSH" in segments and segments["MSH"]:
            msh_segment = segments["MSH"][0]
            msg_type = hl7_mapper_service.extract_segment_field(msh_segment, 9)
            if msg_type and "^" in msg_type:
                msg_type_parts = msg_type.split("^")
                message_type = "transaction" if msg_type_parts[0] in ["ADT", "ORU", "ORM"] else "message"

        # Initialize resources list
        resources = []

        # Generic FHIR resource creation based on HL7 segments
        try:
            created_resources = await _create_fhir_resources_from_segments(segments)
            resources.extend(created_resources)
            logger.info(f"Created {len(created_resources)} FHIR resources from segments: {list(segments.keys())}")
        except Exception as e:
            logger.error(f"Error creating FHIR resources from segments: {e}")
            # Fallback to basic resource creation if generic fails
            if "PID" in segments:
                patient_resource = await _create_patient_from_pid(segments["PID"][0])
                resources.append(patient_resource)
            if "OBX" in segments:
                for i, obx_segment in enumerate(segments["OBX"]):
                    observation_resource = await _create_observation_from_obx(obx_segment, i, patient_resource if 'patient_resource' in locals() else None)
                    resources.append(observation_resource)

        # Create FHIR Bundle
        fhir_bundle = {
            "resourceType": "Bundle",
            "id": f"bundle-{uuid.uuid4()}",
            "meta": {
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            },
            "type": message_type,
            "entry": [
                {
                    "resource": resource,
                    "fullUrl": f"urn:uuid:{resource['id']}"
                }
                for resource in resources
            ]
        }

        # Build Appointment for SIU messages if SCH present
        try:
            if "SCH" in segments and segments["SCH"]:
                sch_segment = segments["SCH"][0]
                pv1_segment = segments.get("PV1", [None])[0] if segments.get("PV1") else None
                ail_segments = segments.get("AIL", [])
                aip_segments = segments.get("AIP", [])

                patient_resource = next((r for r in resources if r.get("resourceType") == "Patient"), None)
                appointment, related = await _create_appointment_from_siu(
                    sch_segment, patient_resource, pv1_segment, ail_segments, aip_segments
                )
                if related:
                    resources.extend(related)
                if appointment:
                    resources.append(appointment)
                # Refresh bundle entries
                fhir_bundle["entry"] = [
                    {"resource": r, "fullUrl": f"urn:uuid:{r['id']}"} for r in resources
                ]
        except Exception as e:
            logger.error(f"Error building Appointment from SIU: {e}")

        # Apply custom mappings if specified (support dict or list safely)
        if isinstance(mapping_config, dict):
            iterator = mapping_config.items()
        elif isinstance(mapping_config, list):
            # Expect list of mapping objects with 'target' (FHIR field), 'segment', 'field', 'transform'
            iterator = [
                (m.get('target') or m.get('fhir_field'), m) for m in mapping_config if isinstance(m, dict)
            ]
        else:
            iterator = []

        for fhir_field, mapping in iterator:
            if not fhir_field or not isinstance(mapping, dict):
                continue
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
                "fhir_resource": resources[0] if resources else None,  # Keep for backward compatibility
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
    Enhanced HL7 to CSV conversion activity
    User creates custom headers and decides what segment to put in each header
    Can use global variables defined in parser activity or hardcode strings
    Supports configurable delimiters and header inclusion options
    """
    config = activity.get("config", {})
    headers = config.get("headers", [])
    mappings = config.get("mappings", {})

    # Enhanced CSV options (merged from CSV Converter)
    delimiter = config.get("delimiter", ",")  # Support configurable delimiter
    include_headers = config.get("include_headers", True)  # Support header inclusion option

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

        # Convert to CSV string with configurable options
        csv_string = _convert_to_csv_string(csv_data, headers, delimiter=delimiter, include_headers=include_headers)

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
            # HL7 paths are written as 1-based components (e.g., PID.5.1 => first component)
            component = (int(parts[2]) - 1) if len(parts) > 2 else 0
            if component < 0:
                component = 0

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


def _convert_to_csv_string(data: List[Dict], headers: List[str], delimiter: str = ",", include_headers: bool = True) -> str:
    """Convert list of dictionaries to CSV string with configurable options"""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers, delimiter=delimiter)
    if include_headers:
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


def _format_hl7_datetime_to_fhir(hl7_datetime: str) -> str:
    """Format HL7 datetime to FHIR datetime format"""
    if not hl7_datetime or len(hl7_datetime) < 8:
        return ""

    try:
        # HL7 datetime format: YYYYMMDDHHMMSS
        year = hl7_datetime[:4]
        month = hl7_datetime[4:6]
        day = hl7_datetime[6:8]

        # Check if time is included
        if len(hl7_datetime) >= 14:
            hour = hl7_datetime[8:10]
            minute = hl7_datetime[10:12]
            second = hl7_datetime[12:14]
            return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"
        else:
            return f"{year}-{month}-{day}"
    except:
        return hl7_datetime


async def _create_fhir_resources_from_segments(segments: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """
    Truly generic FHIR resource creator from HL7 segments
    Handles ANY HL7 message type and creates appropriate FHIR resources dynamically
    """
    resources = []
    patient_resource = None
    encounter_resource = None

    # Step 1: Create Patient resource from PID segment (foundational)
    if "PID" in segments and segments["PID"]:
        patient_resource = await _create_patient_from_pid(segments["PID"][0])
        resources.append(patient_resource)

    # Step 2: Create Encounter resource from PV1 segment
    if "PV1" in segments and segments["PV1"]:
        encounter_resource = await _create_encounter_from_pv1(segments["PV1"][0], patient_resource)
        resources.append(encounter_resource)

    # Step 3: Create Observation resources from OBX segments
    if "OBX" in segments and segments["OBX"]:
        for i, obx_segment in enumerate(segments["OBX"]):
            observation_resource = await _create_observation_from_obx(obx_segment, i, patient_resource)
            resources.append(observation_resource)

    # Step 4: Create DiagnosticReport resource from OBR segments
    if "OBR" in segments and segments["OBR"]:
        for i, obr_segment in enumerate(segments["OBR"]):
            diagnostic_report = await _create_diagnostic_report_from_obr(obr_segment, i, patient_resource)
            resources.append(diagnostic_report)

    # Step 5: Create ServiceRequest resources from ORC segments
    if "ORC" in segments and segments["ORC"]:
        for i, orc_segment in enumerate(segments["ORC"]):
            service_request = await _create_service_request_from_orc(orc_segment, i, patient_resource)
            resources.append(service_request)

    # Step 6: Handle NTE segments (Notes/Comments) -> FHIR Annotation/DocumentReference
    if "NTE" in segments and segments["NTE"]:
        nte_resources = await _create_document_reference_from_nte(segments["NTE"], patient_resource, encounter_resource)
        resources.extend(nte_resources)

    # Step 7: Handle additional common segments dynamically
    # NK1 -> RelatedPerson
    if "NK1" in segments and segments["NK1"]:
        for i, nk1_segment in enumerate(segments["NK1"]):
            related_person = await _create_related_person_from_nk1(nk1_segment, i, patient_resource)
            resources.append(related_person)

    # Step 8: Handle AL1 segments (Allergy Information) -> AllergyIntolerance
    if "AL1" in segments and segments["AL1"]:
        for i, al1_segment in enumerate(segments["AL1"]):
            allergy = await _create_allergy_intolerance_from_al1(al1_segment, i, patient_resource)
            resources.append(allergy)

    # Step 9: Handle DG1 segments (Diagnosis) -> Condition
    if "DG1" in segments and segments["DG1"]:
        for i, dg1_segment in enumerate(segments["DG1"]):
            condition = await _create_condition_from_dg1(dg1_segment, i, patient_resource, encounter_resource)
            resources.append(condition)

    # Step 10: Handle PR1 segments (Procedures) -> Procedure
    if "PR1" in segments and segments["PR1"]:
        for i, pr1_segment in enumerate(segments["PR1"]):
            procedure = await _create_procedure_from_pr1(pr1_segment, i, patient_resource, encounter_resource)
            resources.append(procedure)

    # Step 11: Handle IN1/IN2 segments (Insurance) -> Coverage
    if "IN1" in segments and segments["IN1"]:
        for i, in1_segment in enumerate(segments["IN1"]):
            coverage = await _create_coverage_from_in1(in1_segment, i, patient_resource)
            resources.append(coverage)

    # Step 12: Handle MSA/ERR segments (Application Acknowledgment/Error) -> OperationOutcome
    if "MSA" in segments and segments["MSA"] or "ERR" in segments and segments["ERR"]:
        operation_outcome = await _create_operation_outcome_from_ack(segments)
        resources.append(operation_outcome)

    # Step 13: Handle SFT segments (Software) -> Device
    if "SFT" in segments and segments["SFT"]:
        for i, sft_segment in enumerate(segments["SFT"]):
            device = await _create_device_from_sft(sft_segment, i)
            resources.append(device)

    # Step 14: Handle UAC segments (User Authentication) -> AuditEvent
    if "UAC" in segments and segments["UAC"]:
        for i, uac_segment in enumerate(segments["UAC"]):
            audit_event = await _create_audit_event_from_uac(uac_segment, i, patient_resource)
            resources.append(audit_event)

    # Step 15: Handle EVN segments (Event Type) -> Provenance
    if "EVN" in segments and segments["EVN"]:
        for i, evn_segment in enumerate(segments["EVN"]):
            provenance = await _create_provenance_from_evn(evn_segment, i, patient_resource)
            resources.append(provenance)

    # Step 16: Handle ARV segments (Access Restriction) -> Consent
    if "ARV" in segments and segments["ARV"]:
        for i, arv_segment in enumerate(segments["ARV"]):
            consent = await _create_consent_from_arv(arv_segment, i, patient_resource)
            resources.append(consent)

    # Step 17: Handle PD1 segments (Patient Additional Demographics) -> Enhance Patient
    if "PD1" in segments and segments["PD1"] and patient_resource:
        await _enhance_patient_from_pd1(patient_resource, segments["PD1"][0])

    # Step 18: Handle PV2 segments (Patient Visit Additional Info) -> Enhance Encounter
    if "PV2" in segments and segments["PV2"] and encounter_resource:
        await _enhance_encounter_from_pv2(encounter_resource, segments["PV2"][0])

    # Step 19: Handle ROL segments (Role) -> PractitionerRole
    if "ROL" in segments and segments["ROL"]:
        for i, rol_segment in enumerate(segments["ROL"]):
            practitioner_role = await _create_practitioner_role_from_rol(rol_segment, i, patient_resource, encounter_resource)
            resources.append(practitioner_role)

    # Step 20: Handle ACC segments (Accident) -> Condition (accident)
    if "ACC" in segments and segments["ACC"]:
        for i, acc_segment in enumerate(segments["ACC"]):
            accident_condition = await _create_accident_condition_from_acc(acc_segment, i, patient_resource, encounter_resource)
            resources.append(accident_condition)

    # Step 21: Handle DRG segments (Diagnosis Related Group) -> Account
    if "DRG" in segments and segments["DRG"]:
        for i, drg_segment in enumerate(segments["DRG"]):
            account = await _create_account_from_drg(drg_segment, i, patient_resource, encounter_resource)
            resources.append(account)

    # Step 22: Handle Medication segments (RXE, RXA) -> Medication resources
    medication_resources = await _create_medication_resources_from_segments(segments, patient_resource, encounter_resource)
    resources.extend(medication_resources)

    # Step 23: Handle SPM/SAC segments (Specimen) -> Specimen
    if "SPM" in segments and segments["SPM"]:
        for i, spm_segment in enumerate(segments["SPM"]):
            specimen = await _create_specimen_from_spm(spm_segment, i, patient_resource)
            resources.append(specimen)

    # Step 24: Handle GT1 segments (Guarantor) -> RelatedPerson/Organization
    if "GT1" in segments and segments["GT1"]:
        for i, gt1_segment in enumerate(segments["GT1"]):
            guarantor = await _create_guarantor_from_gt1(gt1_segment, i, patient_resource)
            resources.append(guarantor)

    # Step 25: Handle CTI segments (Clinical Trial) -> ResearchStudy/ResearchSubject
    if "CTI" in segments and segments["CTI"]:
        research_resources = await _create_research_resources_from_cti(segments["CTI"], patient_resource)
        resources.extend(research_resources)

    # Step 26: Handle Z-segments (Custom segments) -> Extensions
    z_segments = {k: v for k, v in segments.items() if k.startswith("Z")}
    if z_segments:
        extensions = await _create_extensions_from_z_segments(z_segments)
        # Add extensions to relevant resources (primarily Patient and Encounter)
        if patient_resource and extensions:
            if "extension" not in patient_resource:
                patient_resource["extension"] = []
            patient_resource["extension"].extend(extensions)

    # Step 27: Create Practitioner and Organization resources from provider/facility references
    practitioners = await _create_practitioners_from_segments(segments)
    resources.extend(practitioners)

    organizations = await _create_organizations_from_segments(segments)
    resources.extend(organizations)

    return resources


async def _create_patient_from_pid(pid_segment: str) -> Dict[str, Any]:
    """Create FHIR Patient resource from PID segment"""
    # Extract the full PID.3 field which may contain multiple identifiers
    full_patient_id = hl7_mapper_service.extract_segment_field(pid_segment, 3)  # PID.3

    # Parse the primary patient ID (first identifier before repetition separator)
    if "~" in full_patient_id:
        # Multiple identifiers - use the first one
        first_identifier = full_patient_id.split("~")[0]
        # Extract the primary ID (first component before ^)
        patient_id = first_identifier.split("^")[0] if "^" in first_identifier else first_identifier
    else:
        # Single identifier - extract primary ID component
        patient_id = full_patient_id.split("^")[0] if "^" in full_patient_id else full_patient_id

    patient_name = hl7_mapper_service.extract_segment_field(pid_segment, 5)  # PID.5
    patient_dob = hl7_mapper_service.extract_segment_field(pid_segment, 7)  # PID.7
    patient_gender = hl7_mapper_service.extract_segment_field(pid_segment, 8)  # PID.8
    patient_address = hl7_mapper_service.extract_segment_field(pid_segment, 11)  # PID.11

    # Parse name
    patient_family = ""
    patient_given = ""
    if patient_name:
        name_parts = patient_name.split("^")
        if len(name_parts) >= 2:
            patient_family = name_parts[0]
            patient_given = name_parts[1]

    patient_resource = {
        "resourceType": "Patient",
        "id": patient_id or f"patient-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        }
    }

    # Add identifiers - handle multiple patient identifiers from PID.3
    if full_patient_id:
        identifiers = []

        # Parse all identifiers from PID.3 field
        if "~" in full_patient_id:
            # Multiple identifiers separated by ~
            identifier_parts = full_patient_id.split("~")
        else:
            # Single identifier
            identifier_parts = [full_patient_id]

        for idx, identifier_part in enumerate(identifier_parts):
            if identifier_part.strip():
                components = identifier_part.split("^")
                id_value = components[0] if components else identifier_part
                assigning_authority = components[3] if len(components) > 3 else ""
                identifier_type = components[4] if len(components) > 4 else ""

                # Create FHIR identifier
                fhir_identifier = {
                    "value": id_value
                }

                # Add use - primary for first identifier
                if idx == 0:
                    fhir_identifier["use"] = "usual"

                # Add type based on HL7 identifier type
                if identifier_type:
                    if identifier_type == "MR":
                        fhir_identifier["type"] = {
                            "coding": [{
                                "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                                "code": "MR",
                                "display": "Medical Record Number"
                            }]
                        }
                    elif identifier_type == "NI":
                        fhir_identifier["type"] = {
                            "coding": [{
                                "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                                "code": "NI",
                                "display": "National unique individual identifier"
                            }]
                        }
                    elif identifier_type == "SS":
                        fhir_identifier["type"] = {
                            "coding": [{
                                "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                                "code": "SS",
                                "display": "Social Security Number"
                            }]
                        }

                # Add system based on assigning authority
                if assigning_authority:
                    if assigning_authority == "HOSP":
                        fhir_identifier["system"] = "http://hospital.example.org/patients"
                    elif assigning_authority == "NATID":
                        fhir_identifier["system"] = "http://national-id.gov/patients"
                    elif assigning_authority == "MOH":
                        fhir_identifier["system"] = "http://moh.gov/patients"
                    else:
                        fhir_identifier["system"] = f"http://{assigning_authority.lower()}.example.org"
                else:
                    fhir_identifier["system"] = "http://hospital.example.org/patients"

                identifiers.append(fhir_identifier)

        if identifiers:
            patient_resource["identifier"] = identifiers

    # Add name
    if patient_family or patient_given:
        patient_resource["name"] = [{
            "family": patient_family,
            "given": [patient_given] if patient_given else []
        }]

    # Add gender
    if patient_gender:
        patient_resource["gender"] = _map_gender(patient_gender)

    # Add birth date
    if patient_dob:
        patient_resource["birthDate"] = _format_date(patient_dob)

    # Add address
    if patient_address:
        address_parts = patient_address.split("^")
        if len(address_parts) >= 4:
            patient_resource["address"] = [{
                "line": [address_parts[0]] if address_parts[0] else [],
                "city": address_parts[2] if len(address_parts) > 2 else "",
                "state": address_parts[3] if len(address_parts) > 3 else "",
                "postalCode": address_parts[4] if len(address_parts) > 4 else ""
            }]

    return patient_resource


async def _create_encounter_from_pv1(pv1_segment: str, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Encounter resource from PV1 segment"""
    encounter_class = hl7_mapper_service.extract_segment_field(pv1_segment, 2)  # PV1.2

    encounter_resource = {
        "resourceType": "Encounter",
        "id": f"encounter-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "in-progress"
    }

    if patient_resource:
        encounter_resource["subject"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    if encounter_class:
        encounter_resource["class"] = {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": encounter_class.lower(),
            "display": encounter_class
        }

    return encounter_resource


async def _create_observation_from_obx(obx_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Observation resource from OBX segment"""
    value_type = hl7_mapper_service.extract_segment_field(obx_segment, 2)  # OBX.2 Value Type
    observation_id = hl7_mapper_service.extract_segment_field(obx_segment, 3)  # OBX.3
    observation_value = hl7_mapper_service.extract_segment_field(obx_segment, 5)  # OBX.5
    units = hl7_mapper_service.extract_segment_field(obx_segment, 6)  # OBX.6
    reference_range = hl7_mapper_service.extract_segment_field(obx_segment, 7)  # OBX.7
    abnormal_flag = hl7_mapper_service.extract_segment_field(obx_segment, 8)  # OBX.8
    observation_datetime = hl7_mapper_service.extract_segment_field(obx_segment, 14)  # OBX.14

    # Parse observation identifier
    obs_code = ""
    obs_display = ""
    if observation_id:
        if "^" in observation_id:
            obs_parts = observation_id.split("^")
            obs_code = obs_parts[0] if len(obs_parts) > 0 else ""
            obs_display = obs_parts[1] if len(obs_parts) > 1 else obs_code
        else:
            obs_code = observation_id
            obs_display = observation_id

    observation_resource = {
        "resourceType": "Observation",
        "id": f"observation-{index+1}-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "final",
        "code": {
            "coding": [{
                "code": obs_code or None,
                "display": obs_display or None,
                "system": "http://loinc.org"
            }],
            "text": obs_display or obs_code or "Observation"
        }
    }

    if patient_resource:
        observation_resource["subject"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    # Add value based on OBX.2
    if observation_value:
        vt = (value_type or "").upper()
        if vt == "NM":
            try:
                numeric_value = float(observation_value)
                observation_resource["valueQuantity"] = {
                    "value": numeric_value,
                    "unit": (units.split('^')[0] if '^' in units else units) if units else "",
                    "system": "http://unitsofmeasure.org",
                    "code": (units.split('^')[0] if '^' in units else units) if units else ""
                }
            except (ValueError, TypeError):
                observation_resource["valueString"] = observation_value
        elif vt in ("CE", "CWE"):
            # CodeableConcept from CE/CWE (code^display^system)
            parts = observation_value.split('^') if observation_value else []
            code = parts[0] if len(parts) > 0 else ""
            display = parts[1] if len(parts) > 1 else code
            system_hint = parts[2] if len(parts) > 2 else ""
            system_map = {"LN": "http://loinc.org", "UCUM": "http://unitsofmeasure.org", "ICD-10": "http://hl7.org/fhir/sid/icd-10"}
            system = system_map.get(system_hint, None)
            cc = {"coding": [{k: v for k, v in [("system", system), ("code", code or None), ("display", display or None)] if v}]}
            observation_resource["valueCodeableConcept"] = cc
        elif vt in ("FT", "TX"):
            observation_resource["valueString"] = observation_value.replace("~", "; ")
        elif vt in ("TS", "DTM", "DT"):
            observation_resource["valueDateTime"] = _parse_hl7_ts_to_iso(observation_value)
        else:
            # Unsupported or complex types -> valueString fallback
            observation_resource["valueString"] = observation_value

    # Add reference range
    if reference_range and reference_range.strip():
        observation_resource["referenceRange"] = [{
            "text": reference_range
        }]

    # Add interpretation
    if abnormal_flag:
        interpretation_map = {"H": "H", "L": "L", "A": "A", "N": "N"}
        interpretation_code = interpretation_map.get(abnormal_flag.upper(), "N")
        observation_resource["interpretation"] = [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                "code": interpretation_code,
                "display": abnormal_flag
            }]
        }]

    # Add effective datetime
    if observation_datetime:
        formatted_datetime = _format_hl7_datetime_to_fhir(observation_datetime)
        if formatted_datetime:
            observation_resource["effectiveDateTime"] = formatted_datetime

    return observation_resource


async def _create_diagnostic_report_from_obr(obr_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR DiagnosticReport resource from OBR segment"""
    test_id = hl7_mapper_service.extract_segment_field(obr_segment, 4)  # OBR.4
    observation_datetime = hl7_mapper_service.extract_segment_field(obr_segment, 7)  # OBR.7

    # Parse test identifier
    test_code = test_id
    test_display = test_id
    if "^" in test_id:
        test_parts = test_id.split("^")
        test_code = test_parts[0] if len(test_parts) > 0 else test_id
        test_display = test_parts[1] if len(test_parts) > 1 else test_code

    diagnostic_report = {
        "resourceType": "DiagnosticReport",
        "id": f"diagnosticreport-{index+1}-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "final",
        "code": {
            "coding": [{
                "code": test_code,
                "display": test_display,
                "system": "http://loinc.org"
            }]
        }
    }

    if patient_resource:
        diagnostic_report["subject"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    if observation_datetime:
        formatted_datetime = _format_hl7_datetime_to_fhir(observation_datetime)
        if formatted_datetime:
            diagnostic_report["effectiveDateTime"] = formatted_datetime

    return diagnostic_report


async def process_hl7_ack_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Generate an HL7 ACK/NACK for the current raw message.

    Config:
      - code: 'AA' | 'AE' | 'AR' (default 'AA')
      - include_err: boolean (default false)
      - error_text: string (included when include_err=true and code != 'AA')
      - profile: string (reserved)
    """
    cfg = activity.get('config', {}) or {}
    code = str(cfg.get('code') or 'AA').upper()
    include_err = bool(cfg.get('include_err', False))
    error_text = str(cfg.get('error_text') or '') if include_err and code in ('AE', 'AR') else None
    profile = str(cfg.get('profile') or 'default')

    if not context.raw_message:
        return ActivityResult(status=ActivityStatus.FAILED, error_message='No raw HL7 message to ACK')

    try:
        ack = generate_ack(context.raw_message, code=code, error_text=error_text, profile=profile)
        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={'ack_message': ack, 'ack_code': code, 'profile': profile},
            variables={'ack_message': ack, 'ack_code': code}
        )
    except Exception as e:
        return ActivityResult(status=ActivityStatus.FAILED, error_message=str(e))


async def _create_service_request_from_orc(orc_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR ServiceRequest resource from ORC segment"""
    order_control = hl7_mapper_service.extract_segment_field(orc_segment, 1)  # ORC.1
    placer_order_number = hl7_mapper_service.extract_segment_field(orc_segment, 2)  # ORC.2

    service_request = {
        "resourceType": "ServiceRequest",
        "id": f"servicerequest-{index+1}-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "active",
        "intent": "order"
    }

    if patient_resource:
        service_request["subject"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    if placer_order_number:
        service_request["identifier"] = [{
            "system": "http://hospital.example.org/orders",
            "value": placer_order_number
        }]

    return service_request


async def _create_related_person_from_nk1(nk1_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR RelatedPerson resource from NK1 segment"""
    nk1_name = hl7_mapper_service.extract_segment_field(nk1_segment, 2)  # NK1.2
    relationship = hl7_mapper_service.extract_segment_field(nk1_segment, 3)  # NK1.3

    # Parse name
    family_name = ""
    given_name = ""
    if nk1_name:
        name_parts = nk1_name.split("^")
        if len(name_parts) >= 2:
            family_name = name_parts[0]
            given_name = name_parts[1]

    related_person = {
        "resourceType": "RelatedPerson",
        "id": f"relatedperson-{index+1}-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        }
    }

    if patient_resource:
        related_person["patient"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    if family_name or given_name:
        related_person["name"] = [{
            "family": family_name,
            "given": [given_name] if given_name else []
        }]

    if relationship:
        related_person["relationship"] = [{
            "coding": [{
                "code": relationship,
                "display": relationship,
                "system": "http://terminology.hl7.org/CodeSystem/v3-RoleCode"
            }]
        }]

    return related_person


async def _create_practitioners_from_segments(segments: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """Create FHIR Practitioner resources from provider references in various segments"""
    practitioners = []
    # This would extract provider references from PV1, OBR, etc. and create Practitioner resources
    # Implementation depends on specific requirements
    return practitioners


async def _create_organizations_from_segments(segments: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """Create FHIR Organization resources from facility references"""
    organizations = []
    # This would extract facility references from MSH and create Organization resources
    # Implementation depends on specific requirements
    return organizations


async def _create_document_reference_from_nte(nte_segments: List[str], patient_resource: Dict[str, Any], encounter_resource: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create FHIR DocumentReference resource from NTE segments (Notes/Comments)"""
    if not nte_segments:
        return []

    # Combine all NTE segments into a single document
    all_notes = []
    for nte_segment in nte_segments:
        note_text = hl7_mapper_service.extract_segment_field(nte_segment, 3)  # NTE.3 - Comment
        if note_text:
            all_notes.append(note_text)

    if not all_notes:
        return []

    # Create a single DocumentReference with all notes
    document_reference = {
        "resourceType": "DocumentReference",
        "id": f"document-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "current",
        "type": {
            "coding": [{
                "system": "http://loinc.org",
                "code": "11506-3",
                "display": "Progress note"
            }]
        },
        "category": [{
            "coding": [{
                "system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
                "code": "clinical-note",
                "display": "Clinical Note"
            }]
        }],
        "content": [{
            "attachment": {
                "contentType": "text/plain",
                "data": "\n".join(all_notes),
                "title": f"Clinical Notes ({len(all_notes)} entries)"
            }
        }]
    }

    if patient_resource:
        document_reference["subject"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    if encounter_resource:
        document_reference["context"] = {
            "encounter": [{
                "reference": f"Encounter/{encounter_resource['id']}"
            }]
        }

    return [document_reference]


async def _create_allergy_intolerance_from_al1(al1_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR AllergyIntolerance resource from AL1 segment"""
    allergen = hl7_mapper_service.extract_segment_field(al1_segment, 3)  # AL1.3 - Allergen
    allergy_type = hl7_mapper_service.extract_segment_field(al1_segment, 2)  # AL1.2 - Allergen Type
    severity = hl7_mapper_service.extract_segment_field(al1_segment, 4)  # AL1.4 - Allergy Severity

    allergy_intolerance = {
        "resourceType": "AllergyIntolerance",
        "id": f"allergy-{index+1}-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "clinicalStatus": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                "code": "active",
                "display": "Active"
            }]
        },
        "verificationStatus": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                "code": "confirmed",
                "display": "Confirmed"
            }]
        },
        "code": {
            "text": allergen if allergen else "Unknown allergen"
        }
    }

    if patient_resource:
        allergy_intolerance["patient"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    return allergy_intolerance


async def _create_condition_from_dg1(dg1_segment: str, index: int, patient_resource: Dict[str, Any], encounter_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Condition resource from DG1 segment"""
    diagnosis_code = hl7_mapper_service.extract_segment_field(dg1_segment, 3)  # DG1.3 - Diagnosis Code
    diagnosis_description = hl7_mapper_service.extract_segment_field(dg1_segment, 4)  # DG1.4 - Diagnosis Description

    condition = {
        "resourceType": "Condition",
        "id": f"condition-{index+1}-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "clinicalStatus": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                "code": "active",
                "display": "Active"
            }]
        },
        "verificationStatus": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                "code": "confirmed",
                "display": "Confirmed"
            }]
        },
        "code": {
            "coding": [{
                "code": diagnosis_code if diagnosis_code else "unknown",
                "display": diagnosis_description if diagnosis_description else "Unknown condition",
                "system": "http://hl7.org/fhir/sid/icd-10"
            }]
        }
    }

    if patient_resource:
        condition["subject"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    if encounter_resource:
        condition["encounter"] = {
            "reference": f"Encounter/{encounter_resource['id']}"
        }

    return condition


async def _create_procedure_from_pr1(pr1_segment: str, index: int, patient_resource: Dict[str, Any], encounter_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Procedure resource from PR1 segment"""
    procedure_code = hl7_mapper_service.extract_segment_field(pr1_segment, 3)  # PR1.3 - Procedure Code
    procedure_description = hl7_mapper_service.extract_segment_field(pr1_segment, 4)  # PR1.4 - Procedure Description
    procedure_date = hl7_mapper_service.extract_segment_field(pr1_segment, 5)  # PR1.5 - Procedure Date

    procedure = {
        "resourceType": "Procedure",
        "id": f"procedure-{index+1}-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "completed",
        "code": {
            "coding": [{
                "code": procedure_code if procedure_code else "unknown",
                "display": procedure_description if procedure_description else "Unknown procedure",
                "system": "http://www.cms.gov/Medicare/Coding/ICD10"
            }]
        }
    }

    if patient_resource:
        procedure["subject"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    if encounter_resource:
        procedure["encounter"] = {
            "reference": f"Encounter/{encounter_resource['id']}"
        }

    if procedure_date:
        formatted_date = _format_hl7_datetime_to_fhir(procedure_date)
        if formatted_date:
            procedure["performedDateTime"] = formatted_date

    return procedure


async def _create_coverage_from_in1(in1_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Coverage resource from IN1 segment"""
    plan_id = hl7_mapper_service.extract_segment_field(in1_segment, 2)  # IN1.2 - Insurance Plan ID
    company_name = hl7_mapper_service.extract_segment_field(in1_segment, 4)  # IN1.4 - Insurance Company Name

    coverage = {
        "resourceType": "Coverage",
        "id": f"coverage-{index+1}-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "active",
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "HIP",
                "display": "Health Insurance Plan"
            }]
        }
    }

    if patient_resource:
        coverage["beneficiary"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    if plan_id:
        coverage["identifier"] = [{
            "system": "http://hospital.example.org/insurance",
            "value": plan_id
        }]

    if company_name:
        coverage["payor"] = [{
            "display": company_name
        }]

    return coverage


async def _create_operation_outcome_from_ack(segments: Dict[str, List[str]]) -> Dict[str, Any]:
    """Create FHIR OperationOutcome resource from MSA/ERR segments"""
    operation_outcome = {
        "resourceType": "OperationOutcome",
        "id": f"outcome-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "issue": []
    }

    # Process MSA segments
    if "MSA" in segments:
        for msa_segment in segments["MSA"]:
            ack_code = hl7_mapper_service.extract_segment_field(msa_segment, 1)  # MSA.1 - Acknowledgment Code
            issue = {
                "severity": "information" if ack_code == "AA" else "error",
                "code": "processing",
                "details": {
                    "text": f"Message acknowledgment: {ack_code}"
                }
            }
            operation_outcome["issue"].append(issue)

    # Process ERR segments
    if "ERR" in segments:
        for err_segment in segments["ERR"]:
            error_code = hl7_mapper_service.extract_segment_field(err_segment, 4)  # ERR.4 - Error Code
            error_text = hl7_mapper_service.extract_segment_field(err_segment, 8)  # ERR.8 - User Message
            issue = {
                "severity": "error",
                "code": "processing",
                "details": {
                    "coding": [{
                        "code": error_code if error_code else "unknown",
                        "display": error_text if error_text else "Unknown error"
                    }]
                }
            }
            operation_outcome["issue"].append(issue)

    return operation_outcome


async def _create_document_reference_from_nte(nte_segments: List[str], patient_resource: Dict[str, Any], encounter_resource: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create FHIR DocumentReference resource from NTE segments (Notes/Comments)"""
    if not nte_segments:
        return []

    # Combine all NTE segments into a single document
    all_notes = []
    note_type = "general"  # Default note type

    for nte_segment in nte_segments:
        # NTE.1 - Set ID (optional)
        set_id = hl7_mapper_service.extract_segment_field(nte_segment, 1)

        # NTE.2 - Source of Comment (optional)
        source = hl7_mapper_service.extract_segment_field(nte_segment, 2)
        if source and "clinical" in source.lower():
            note_type = "clinical"
        elif source and "lab" in source.lower():
            note_type = "laboratory"

        # NTE.3 - Comment - this is the main content
        note_text = hl7_mapper_service.extract_segment_field(nte_segment, 3)
        if note_text:
            all_notes.append(note_text)

    if not all_notes:
        return []

    # Create a single DocumentReference for all notes
    document_reference = {
        "resourceType": "DocumentReference",
        "id": f"doc-{uuid.uuid4()}",
        "meta": {
            "versionId": "1",
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "current",
        "type": {
            "coding": [{
                "system": "http://loinc.org",
                "code": "11506-3" if note_type == "clinical" else "34109-9",
                "display": "Progress note" if note_type == "clinical" else "Note"
            }]
        },
        "category": [{
            "coding": [{
                "system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
                "code": note_type,
                "display": note_type.title()
            }]
        }],
        "subject": {
            "reference": f"Patient/{patient_resource.get('id', 'unknown')}"
        },
        "date": datetime.utcnow().isoformat() + "Z",
        "content": [{
            "attachment": {
                "contentType": "text/plain",
                "data": base64.b64encode('\n'.join(all_notes).encode('utf-8')).decode('utf-8')
            }
        }]
    }

    # Add context if encounter exists
    if encounter_resource:
        document_reference["context"] = {
            "encounter": [{
                "reference": f"Encounter/{encounter_resource.get('id', 'unknown')}"
            }]
        }

    return [document_reference]


def _parse_hl7_ts_to_iso(ts: str) -> str:
    """Parse HL7 TS (YYYYMMDDHHMMSS[+/-ZZZZ]) to ISO 8601 string."""
    if not ts:
        return ""
    try:
        base = ts[:14]
        dt = datetime.strptime(base, "%Y%m%d%H%M%S")
        return dt.isoformat() + "Z"
    except Exception:
        try:
            base = ts[:8]
            dt = datetime.strptime(base, "%Y%m%d")
            return dt.date().isoformat()
        except Exception:
            return ts


async def _create_appointment_from_siu(
    sch_segment: str,
    patient_resource: Optional[Dict[str, Any]],
    pv1_segment: Optional[str],
    ail_segments: List[str],
    aip_segments: List[str],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Create FHIR Appointment from SCH/AIL/AIP segments for SIU messages."""
    related: List[Dict[str, Any]] = []

    reason_text = hl7_mapper_service.extract_segment_field(sch_segment, 7)
    service_ce = hl7_mapper_service.extract_segment_field(sch_segment, 6)
    service_text = service_ce.split('^')[1] if '^' in service_ce and len(service_ce.split('^')) > 1 else service_ce
    duration = hl7_mapper_service.extract_segment_field(sch_segment, 9)
    tq = hl7_mapper_service.extract_segment_field(sch_segment, 11)
    start_iso = end_iso = ""
    if tq and '^' in tq:
        parts = tq.split('^')
        if len(parts) > 3 and parts[3]:
            start_iso = _parse_hl7_ts_to_iso(parts[3])
        if len(parts) > 4 and parts[4]:
            end_iso = _parse_hl7_ts_to_iso(parts[4])
    status_text = hl7_mapper_service.extract_segment_field(sch_segment, 25)
    status_map = {
        'Scheduled': 'booked',
        'Booked': 'booked',
        'Arrived': 'arrived',
        'Cancelled': 'cancelled',
        'No Show': 'noshow'
    }
    appt_status = status_map.get(status_text, 'booked')

    appointment: Dict[str, Any] = {
        "resourceType": "Appointment",
        "id": f"appointment-{uuid.uuid4()}",
        "meta": {"lastUpdated": datetime.utcnow().isoformat() + 'Z'},
        "status": appt_status,
        "participant": []
    }
    if start_iso:
        appointment["start"] = start_iso
    if end_iso:
        appointment["end"] = end_iso
    if service_text:
        appointment["serviceType"] = [{"text": service_text}]
    if reason_text:
        appointment["reason"] = [{"text": reason_text}]
    if duration:
        try:
            appointment["minutesDuration"] = int(duration)
        except Exception:
            pass

    if patient_resource:
        appointment["participant"].append({
            "actor": {"reference": f"Patient/{patient_resource['id']}"},
            "status": "accepted"
        })

    # Location from AIL
    if ail_segments:
        ail = ail_segments[0]
        location_name = hl7_mapper_service.extract_segment_field(ail, 4)  # AIL.4 - description
        name = location_name.split('^')[1] if '^' in location_name and len(location_name.split('^')) > 1 else (location_name or "Location")
        location_resource = {
            "resourceType": "Location",
            "id": f"location-{uuid.uuid4()}",
            "name": name
        }
        related.append(location_resource)
        appointment["participant"].append({
            "actor": {"reference": f"Location/{location_resource['id']}"},
            "status": "accepted"
        })

    # Practitioner from AIP
    if aip_segments:
        aip = aip_segments[0]
        xcn = hl7_mapper_service.extract_segment_field(aip, 3)
        family = given = ""
        if xcn and '^' in xcn:
            parts = xcn.split('^')
            family = parts[1] if len(parts) > 1 else ""
            given = parts[2] if len(parts) > 2 else ""
        practitioner = {
            "resourceType": "Practitioner",
            "id": f"practitioner-{uuid.uuid4()}",
            "name": [{"family": family, "given": [given] if given else []}]
        }
        related.append(practitioner)
        appointment["participant"].append({
            "actor": {"reference": f"Practitioner/{practitioner['id']}"},
            "status": "accepted"
        })

    return appointment, related



async def _create_allergy_intolerance_from_al1(al1_segments: List[str], patient_resource: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create FHIR AllergyIntolerance resources from AL1 segments"""
    resources = []

    for al1_segment in al1_segments:
        # AL1.1 - Set ID
        set_id = hl7_mapper_service.extract_segment_field(al1_segment, 1)

        # AL1.2 - Allergen Type Code
        allergen_type = hl7_mapper_service.extract_segment_field(al1_segment, 2)

        # AL1.3 - Allergen Code/Mnemonic/Description
        allergen_code = hl7_mapper_service.extract_segment_field(al1_segment, 3, 0)
        allergen_text = hl7_mapper_service.extract_segment_field(al1_segment, 3, 1)

        # AL1.4 - Allergy Severity Code
        severity_code = hl7_mapper_service.extract_segment_field(al1_segment, 4)

        # AL1.5 - Allergy Reaction Code
        reaction_code = hl7_mapper_service.extract_segment_field(al1_segment, 5)

        allergy_resource = {
            "resourceType": "AllergyIntolerance",
            "id": f"allergy-{uuid.uuid4()}",
            "meta": {
                "versionId": "1",
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            },
            "clinicalStatus": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    "code": "active",
                    "display": "Active"
                }]
            },
            "verificationStatus": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                    "code": "confirmed",
                    "display": "Confirmed"
                }]
            },
            "type": "allergy" if allergen_type == "DA" else "intolerance",
            "category": ["medication" if allergen_type == "DA" else "food"],
            "criticality": "high" if severity_code in ["SV", "H"] else "low",
            "patient": {
                "reference": f"Patient/{patient_resource.get('id', 'unknown')}"
            },
            "code": {
                "coding": [{
                    "code": allergen_code if allergen_code else "unknown",
                    "display": allergen_text if allergen_text else "Unknown allergen"
                }]
            }
        }

        # Add reaction information if available
        if reaction_code:
            allergy_resource["reaction"] = [{
                "manifestation": [{
                    "coding": [{
                        "code": reaction_code,
                        "display": "Allergic reaction"
                    }]
                }],
                "severity": "severe" if severity_code in ["SV", "H"] else "mild"
            }]

        resources.append(allergy_resource)

    return resources


async def _create_condition_from_dg1(dg1_segments: List[str], patient_resource: Dict[str, Any], encounter_resource: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create FHIR Condition resources from DG1 segments (Diagnosis)"""
    resources = []

    for dg1_segment in dg1_segments:
        # DG1.1 - Set ID
        set_id = hl7_mapper_service.extract_segment_field(dg1_segment, 1)

        # DG1.3 - Diagnosis Code
        diagnosis_code = hl7_mapper_service.extract_segment_field(dg1_segment, 3, 0)
        diagnosis_text = hl7_mapper_service.extract_segment_field(dg1_segment, 3, 1)

        # DG1.4 - Diagnosis Description
        diagnosis_desc = hl7_mapper_service.extract_segment_field(dg1_segment, 4)

        # DG1.5 - Diagnosis Date/Time
        diagnosis_date = hl7_mapper_service.extract_segment_field(dg1_segment, 5)

        # DG1.6 - Diagnosis Type
        diagnosis_type = hl7_mapper_service.extract_segment_field(dg1_segment, 6)

        condition_resource = {
            "resourceType": "Condition",
            "id": f"condition-{uuid.uuid4()}",
            "meta": {
                "versionId": "1",
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            },
            "clinicalStatus": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                    "display": "Active"
                }]
            },
            "verificationStatus": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                    "code": "confirmed",
                    "display": "Confirmed"
                }]
            },
            "category": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/condition-category",
                    "code": "problem-list-item" if diagnosis_type == "F" else "encounter-diagnosis",
                    "display": "Problem List Item" if diagnosis_type == "F" else "Encounter Diagnosis"
                }]
            }],
            "code": {
                "coding": [{
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "code": diagnosis_code if diagnosis_code else "unknown",
                    "display": diagnosis_text if diagnosis_text else diagnosis_desc if diagnosis_desc else "Unknown condition"
                }]
            },
            "subject": {
                "reference": f"Patient/{patient_resource.get('id', 'unknown')}"
            }
        }

        # Add encounter context if available
        if encounter_resource:
            condition_resource["encounter"] = {
                "reference": f"Encounter/{encounter_resource.get('id', 'unknown')}"
            }

        # Add onset date if available
        if diagnosis_date:
            try:
                parsed_date = _parse_hl7_datetime(diagnosis_date)
                if parsed_date:
                    condition_resource["onsetDateTime"] = parsed_date
            except:
                pass

        resources.append(condition_resource)

    return resources


async def _create_procedure_from_pr1(pr1_segments: List[str], patient_resource: Dict[str, Any], encounter_resource: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create FHIR Procedure resources from PR1 segments"""
    resources = []

    for pr1_segment in pr1_segments:
        # PR1.1 - Set ID
        set_id = hl7_mapper_service.extract_segment_field(pr1_segment, 1)

        # PR1.3 - Procedure Code
        procedure_code = hl7_mapper_service.extract_segment_field(pr1_segment, 3, 0)
        procedure_text = hl7_mapper_service.extract_segment_field(pr1_segment, 3, 1)

        # PR1.4 - Procedure Description
        procedure_desc = hl7_mapper_service.extract_segment_field(pr1_segment, 4)

        # PR1.5 - Procedure Date/Time
        procedure_date = hl7_mapper_service.extract_segment_field(pr1_segment, 5)

        # PR1.11 - Surgeon
        surgeon = hl7_mapper_service.extract_segment_field(pr1_segment, 11)

        procedure_resource = {
            "resourceType": "Procedure",
            "id": f"procedure-{uuid.uuid4()}",
            "meta": {
                "versionId": "1",
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            },
            "status": "completed",
            "code": {
                "coding": [{
                    "system": "http://www.ama-assn.org/go/cpt",
                    "code": procedure_code if procedure_code else "unknown",
                    "display": procedure_text if procedure_text else procedure_desc if procedure_desc else "Unknown procedure"
                }]
            },
            "subject": {
                "reference": f"Patient/{patient_resource.get('id', 'unknown')}"
            }
        }

        # Add encounter context if available
        if encounter_resource:
            procedure_resource["encounter"] = {
                "reference": f"Encounter/{encounter_resource.get('id', 'unknown')}"
            }

        # Add performed date if available
        if procedure_date:
            try:
                parsed_date = _parse_hl7_datetime(procedure_date)
                if parsed_date:
                    procedure_resource["performedDateTime"] = parsed_date
            except:
                pass

        # Add performer if surgeon specified
        if surgeon:
            procedure_resource["performer"] = [{
                "actor": {
                    "display": surgeon
                }
            }]

        resources.append(procedure_resource)

    return resources


async def _create_coverage_from_in1(in1_segments: List[str], patient_resource: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create FHIR Coverage resources from IN1 segments (Insurance)"""
    resources = []

    for in1_segment in in1_segments:
        # IN1.1 - Set ID
        set_id = hl7_mapper_service.extract_segment_field(in1_segment, 1)

        # IN1.2 - Insurance Plan ID
        plan_id = hl7_mapper_service.extract_segment_field(in1_segment, 2)

        # IN1.3 - Insurance Company ID
        company_id = hl7_mapper_service.extract_segment_field(in1_segment, 3)

        # IN1.4 - Insurance Company Name
        company_name = hl7_mapper_service.extract_segment_field(in1_segment, 4)

        # IN1.36 - Policy Number
        policy_number = hl7_mapper_service.extract_segment_field(in1_segment, 36)

        coverage_resource = {
            "resourceType": "Coverage",
            "id": f"coverage-{uuid.uuid4()}",
            "meta": {
                "versionId": "1",
                "lastUpdated": datetime.utcnow().isoformat() + "Z"
            },
            "status": "active",
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "EHCPOL",
                    "display": "extended healthcare"
                }]
            },
            "policyHolder": {
                "reference": f"Patient/{patient_resource.get('id', 'unknown')}"
            },
            "subscriber": {
                "reference": f"Patient/{patient_resource.get('id', 'unknown')}"
            },
            "beneficiary": {
                "reference": f"Patient/{patient_resource.get('id', 'unknown')}"
            },
            "relationship": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/subscriber-relationship",
                    "code": "self",
                    "display": "Self"
                }]
            },
            "period": {
                "start": datetime.utcnow().isoformat() + "Z"
            }
        }

        # Add payor information
        if company_name or company_id:
            coverage_resource["payor"] = [{
                "display": company_name if company_name else f"Insurance Company {company_id}"
            }]

        # Add subscriber ID if available
        if policy_number:
            coverage_resource["subscriberId"] = policy_number

        resources.append(coverage_resource)

    return resources


def _parse_hl7_datetime(dt_str: str) -> str:
    """Parse HL7 datetime to ISO format"""
    if not dt_str:
        return ""

    try:
        # Handle different HL7 datetime formats
        if len(dt_str) >= 14:  # YYYYMMDDHHMMSS
            year, month, day = dt_str[:4], dt_str[4:6], dt_str[6:8]
            hour, minute, second = dt_str[8:10], dt_str[10:12], dt_str[12:14]

            # Check for timezone offset
            if len(dt_str) > 14 and dt_str[14] in ['+', '-']:
                tz_offset = dt_str[14:]
                return f"{year}-{month}-{day}T{hour}:{minute}:{second}{tz_offset}"
            else:
                return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"
        elif len(dt_str) >= 8:  # YYYYMMDD
            year, month, day = dt_str[:4], dt_str[4:6], dt_str[6:8]
            return f"{year}-{month}-{day}"
    except:
        pass

    return dt_str


async def _create_device_from_sft(sft_segment: str, index: int) -> Dict[str, Any]:
    """Create FHIR Device resource from SFT segment (Software)"""
    software_vendor = hl7_mapper_service.extract_segment_field(sft_segment, 1)  # SFT.1
    software_version = hl7_mapper_service.extract_segment_field(sft_segment, 2)  # SFT.2
    product_name = hl7_mapper_service.extract_segment_field(sft_segment, 3)  # SFT.3
    binary_id = hl7_mapper_service.extract_segment_field(sft_segment, 4)  # SFT.4
    install_date = hl7_mapper_service.extract_segment_field(sft_segment, 5)  # SFT.5

    device = {
        "resourceType": "Device",
        "id": f"device-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "active",
        "deviceName": [{
            "name": product_name if product_name else "Unknown Software",
            "type": "user-friendly-name"
        }],
        "type": {
            "coding": [{
                "system": "http://snomed.info/sct",
                "code": "706687001",
                "display": "Software"
            }]
        }
    }

    if software_vendor:
        device["manufacturer"] = software_vendor.split("^")[0]  # First component

    if software_version:
        device["version"] = [{
            "value": software_version
        }]

    return device


# Add placeholder implementations for missing functions
# These can be expanded with full implementations as needed

async def _create_audit_event_from_uac(uac_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR AuditEvent resource from UAC segment (User Authentication)"""
    return {
        "resourceType": "AuditEvent",
        "id": f"audit-{uuid.uuid4()}",
        "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"},
        "type": {"system": "http://terminology.hl7.org/CodeSystem/audit-event-type", "code": "110114", "display": "User Authentication"},
        "action": "E", "recorded": datetime.utcnow().isoformat() + "Z", "outcome": "0",
        "agent": [{"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/extra-security-role-type", "code": "authserver", "display": "authorization server"}]}, "who": {"display": "System"}, "requestor": True}]
    }

async def _create_provenance_from_evn(evn_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Provenance resource from EVN segment"""
    return {
        "resourceType": "Provenance", "id": f"provenance-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"},
        "recorded": datetime.utcnow().isoformat() + "Z", "activity": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-DataOperation", "code": "UPDATE", "display": "Update"}]},
        "agent": [{"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type", "code": "performer", "display": "Performer"}]}, "who": {"display": "System"}}],
        "target": [{"reference": f"Patient/{patient_resource['id']}"}] if patient_resource else []
    }

async def _create_consent_from_arv(arv_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Consent resource from ARV segment"""
    return {
        "resourceType": "Consent", "id": f"consent-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "status": "active",
        "scope": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/consentscope", "code": "patient-privacy", "display": "Privacy Consent"}]},
        "category": [{"coding": [{"system": "http://loinc.org", "code": "59284-0", "display": "Patient Consent"}]}],
        "policyRule": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/consentpolicycodes", "code": "hipaa-auth", "display": "HIPAA Authorization"}]},
        "patient": {"reference": f"Patient/{patient_resource['id']}"} if patient_resource else None
    }

async def _enhance_patient_from_pd1(patient_resource: Dict[str, Any], pd1_segment: str):
    """Enhance Patient resource with PD1 segment data"""
    if "extension" not in patient_resource:
        patient_resource["extension"] = []
    patient_resource["extension"].append({"url": "http://hl7.org/fhir/StructureDefinition/patient-pd1", "valueString": "Enhanced with PD1 data"})

async def _enhance_encounter_from_pv2(encounter_resource: Dict[str, Any], pv2_segment: str):
    """Enhance Encounter resource with PV2 segment data"""
    admit_reason = hl7_mapper_service.extract_segment_field(pv2_segment, 3)
    if admit_reason:
        if "reasonCode" not in encounter_resource:
            encounter_resource["reasonCode"] = []
        encounter_resource["reasonCode"].append({"text": admit_reason})

async def _create_practitioner_role_from_rol(rol_segment: str, index: int, patient_resource: Dict[str, Any], encounter_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR PractitionerRole resource from ROL segment"""
    return {
        "resourceType": "PractitionerRole", "id": f"practitioner-role-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "active": True,
        "code": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/practitioner-role", "code": "provider", "display": "Provider"}]}]
    }

async def _create_accident_condition_from_acc(acc_segment: str, index: int, patient_resource: Dict[str, Any], encounter_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Condition resource for accident from ACC segment"""
    return {
        "resourceType": "Condition", "id": f"accident-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"},
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active", "display": "Active"}]},
        "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed", "display": "Confirmed"}]},
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "problem-list-item", "display": "Problem List Item"}]}],
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "217082002", "display": "Accidental injury"}], "text": "Accident"},
        "subject": {"reference": f"Patient/{patient_resource['id']}"} if patient_resource else None
    }

async def _create_account_from_drg(drg_segment: str, index: int, patient_resource: Dict[str, Any], encounter_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Account resource from DRG segment"""
    return {
        "resourceType": "Account", "id": f"account-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "status": "active",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "PBILLACCT", "display": "Patient Billing Account"}]},
        "subject": [{"reference": f"Patient/{patient_resource['id']}"}] if patient_resource else []
    }

async def _create_medication_resources_from_segments(segments: Dict[str, List[str]], patient_resource: Dict[str, Any], encounter_resource: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create FHIR medication-related resources from RXE, RXA, RXC, RXR segments"""
    resources = []
    if "RXE" in segments:
        for i, rxe_segment in enumerate(segments["RXE"]):
            resources.append({
                "resourceType": "MedicationStatement", "id": f"medication-statement-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "status": "active",
                "subject": {"reference": f"Patient/{patient_resource['id']}"} if patient_resource else None,
                "medicationCodeableConcept": {"text": "Medication from RXE segment"}
            })
    if "RXA" in segments:
        for i, rxa_segment in enumerate(segments["RXA"]):
            resources.append({
                "resourceType": "MedicationAdministration", "id": f"medication-admin-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "status": "completed",
                "subject": {"reference": f"Patient/{patient_resource['id']}"} if patient_resource else None,
                "medicationCodeableConcept": {"text": "Medication from RXA segment"},
                "effectiveDateTime": datetime.utcnow().isoformat() + "Z"
            })
    return resources

async def _create_specimen_from_spm(spm_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Specimen resource from SPM segment"""
    specimen_type = hl7_mapper_service.extract_segment_field(spm_segment, 4)
    return {
        "resourceType": "Specimen", "id": f"specimen-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "status": "available",
        "subject": {"reference": f"Patient/{patient_resource['id']}"} if patient_resource else None,
        "type": {"text": specimen_type if specimen_type else "Unknown specimen"}
    }

async def _create_guarantor_from_gt1(gt1_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR RelatedPerson resource from GT1 segment (Guarantor)"""
    guarantor_name = hl7_mapper_service.extract_segment_field(gt1_segment, 3)
    return {
        "resourceType": "RelatedPerson", "id": f"guarantor-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "active": True,
        "patient": {"reference": f"Patient/{patient_resource['id']}"} if patient_resource else None,
        "relationship": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-RoleCode", "code": "GT", "display": "Guarantor"}]}],
        "name": [{"text": guarantor_name}] if guarantor_name else []
    }

async def _create_research_resources_from_cti(cti_segments: List[str], patient_resource: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create FHIR ResearchStudy and ResearchSubject resources from CTI segments"""
    resources = []
    for i, cti_segment in enumerate(cti_segments):
        study_id = hl7_mapper_service.extract_segment_field(cti_segment, 1)
        resources.append({
            "resourceType": "ResearchStudy", "id": f"research-study-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "status": "active",
            "title": f"Clinical Trial {study_id}" if study_id else "Clinical Trial"
        })
        if patient_resource:
            resources.append({
                "resourceType": "ResearchSubject", "id": f"research-subject-{uuid.uuid4()}", "meta": {"lastUpdated": datetime.utcnow().isoformat() + "Z"}, "status": "candidate",
                "study": {"reference": f"ResearchStudy/{resources[-1]['id']}"}, "individual": {"reference": f"Patient/{patient_resource['id']}"}
            })
    return resources

async def _create_extensions_from_z_segments(z_segments: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """Create FHIR extensions from Z segments (custom segments)"""
    extensions = []
    for segment_type, segment_list in z_segments.items():
        for i, segment in enumerate(segment_list):
            fields = segment.split("|")[1:] if "|" in segment else [segment]
            extension = {"url": f"http://hl7.org/fhir/StructureDefinition/{segment_type.lower()}-extension", "extension": []}
            for field_num, field_value in enumerate(fields, 1):
                if field_value:
                    extension["extension"].append({"url": f"field-{field_num}", "valueString": field_value})
            if extension["extension"]:
                extensions.append(extension)
    return extensions


async def process_csv_to_hl7_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Convert CSV rows into HL7 VXU messages (immunization-focused defaults).

    Config:
      - delimiter: string (default ",")
      - has_header: bool (default True)
      - headers: optional list of header names when has_header is False
      - field_mappings: { "PID.3.1": "patient_id", "PID.5.1": "last_name", ... }
      - defaults: { "RXA.5": "998^No Vaccine Administered^CVX" } applied before mappings
      - csv_text / csv_content: inline CSV payload (falls back to context.csv_content/raw_message)
      - message_type: default "VXU^V04"
      - sending_application / sending_facility / receiving_application / receiving_facility
    """
    cfg = activity.get("config", {}) or {}

    def _coerce_dict(val: Any) -> Dict[str, Any]:
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    delimiter = str(cfg.get("delimiter") or ",")
    if delimiter == "\\t":
        delimiter = "\t"
    has_header = bool(cfg.get("has_header", True))
    headers_override = cfg.get("headers") or cfg.get("csv_headers")
    field_mappings = _coerce_dict(cfg.get("field_mappings") or {})
    default_values = _coerce_dict(cfg.get("defaults") or cfg.get("default_values") or {})

    prefer_config_payload = bool(cfg.get("prefer_config_payload", False))

    # Determine CSV text source:
    # - If prefer_config_payload, use activity config first
    # - Otherwise prefer runtime payloads (variables/raw_message) before falling back to config
    if prefer_config_payload:
        csv_text = cfg.get("csv_text") or cfg.get("csv_content") or context.variables.get("csv_text") or context.variables.get("csv_content") or context.raw_message
    else:
        csv_text = (
            context.variables.get("csv_text")
            or context.variables.get("csv_content")
            or context.raw_message
            or cfg.get("csv_text")
            or cfg.get("csv_content")
        )
    csv_rows = cfg.get("rows") or context.variables.get("csv_rows")

    if not csv_text and not csv_rows:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No CSV input provided for csv_to_hl7 activity"
        )

    # Parse CSV rows into list of dicts
    rows: List[Dict[str, Any]] = []
    try:
        if csv_rows and isinstance(csv_rows, list):
            if all(isinstance(r, dict) for r in csv_rows):
                rows = [dict(r) for r in csv_rows]  # shallow copy
            elif all(isinstance(r, (list, tuple)) for r in csv_rows):
                for r in csv_rows:
                    rows.append({f"col_{i+1}": v for i, v in enumerate(r)})
        else:
            stream = io.StringIO(csv_text)
            if has_header or headers_override:
                reader = csv.DictReader(stream, delimiter=delimiter, fieldnames=headers_override)
                for row in reader:
                    rows.append(dict(row))
            else:
                reader = csv.reader(stream, delimiter=delimiter)
                for row in reader:
                    rows.append({f"col_{i+1}": v for i, v in enumerate(row)})
    except Exception as e:
        logger.error(f"csv_to_hl7: failed to parse CSV: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to parse CSV: {str(e)}"
        )

    if not rows:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="CSV contained no rows to convert"
        )

    # Helpers to build HL7 message
    def _set_path(segments: Dict[str, List[str]], path: str, value: Any) -> None:
        if value is None:
            return
        parts = path.split(".")
        if len(parts) < 2:
            return
        segment = parts[0].strip().upper()
        try:
            field_idx = int(parts[1])
        except ValueError:
            return
        component_idx = None
        if len(parts) > 2:
            try:
                component_idx = int(parts[2])
            except ValueError:
                component_idx = None

        fields = segments.setdefault(segment, [])
        pos = max(field_idx - 1, 0)
        while len(fields) <= pos:
            fields.append("")

        if component_idx:
            existing = fields[pos] or ""
            components = existing.split("^") if existing else []
            comp_pos = max(component_idx - 1, 0)
            while len(components) <= comp_pos:
                components.append("")
            components[comp_pos] = str(value)
            fields[pos] = "^".join(components)
        else:
            fields[pos] = str(value)

    def _build_message(segments: Dict[str, List[str]]) -> str:
        order = ["MSH", "PID", "PD1", "NK1", "ORC", "RXA", "RXR", "OBX"]
        lines: List[str] = []
        # Ensure MSH is first if present
        if "MSH" in segments:
            fields = segments["MSH"]
            if len(fields) < 2:
                fields = fields + [""] * (2 - len(fields))
            if not fields[1]:
                fields[1] = "^~\\&"
            # Skip MSH.1 (field separator) – HL7 text already uses pipe separators
            lines.append("MSH|" + "|".join(fields[1:]))

        for seg in order:
            if seg == "MSH":
                continue
            if seg in segments:
                lines.append(f"{seg}|" + "|".join(segments[seg]))
        for seg, fields in segments.items():
            if seg in order:
                continue
            lines.append(f"{seg}|" + "|".join(fields))
        return "\r".join(lines)

    def _get_value(row: Dict[str, Any], source: Any) -> Any:
        if row is None:
            return None
        if isinstance(source, str) and source in row:
            return row.get(source)
        # Allow numeric indexes as strings or ints for headerless CSVs
        try:
            idx = int(source)
            key = list(row.keys())[idx] if idx < len(row) else None
            return row.get(key) if key else None
        except Exception:
            return row.get(str(source)) if isinstance(source, (str, int)) else None

    # Prepare defaults for immunization VXU messages
    now_ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    message_type = cfg.get("message_type") or "VXU^V04"
    sending_application = cfg.get("sending_application") or "CSV2HL7"
    sending_facility = cfg.get("sending_facility") or "CSV_PIPELINE"
    receiving_application = cfg.get("receiving_application") or "ICARE"
    receiving_facility = cfg.get("receiving_facility") or "IL-DOH"
    processing_id = cfg.get("processing_id") or "P"
    version = cfg.get("version") or "2.5.1"

    hl7_messages: List[str] = []
    for idx, row in enumerate(rows):
        segments: Dict[str, List[str]] = {}
        message_control_id = cfg.get("message_control_id") or f"CSV{uuid.uuid4().hex[:10]}"

        # Baseline defaults
        _set_path(segments, "MSH.2", "^~\\&")
        _set_path(segments, "MSH.3", sending_application)
        _set_path(segments, "MSH.4", sending_facility)
        _set_path(segments, "MSH.5", receiving_application)
        _set_path(segments, "MSH.6", receiving_facility)
        _set_path(segments, "MSH.7", now_ts)
        _set_path(segments, "MSH.9", message_type)
        _set_path(segments, "MSH.10", message_control_id)
        _set_path(segments, "MSH.11", processing_id)
        _set_path(segments, "MSH.12", version)
        _set_path(segments, "MSH.15", "AL")
        _set_path(segments, "MSH.16", "NE")

        _set_path(segments, "ORC.1", "RE")
        _set_path(segments, "RXA.1", "0")
        _set_path(segments, "RXA.2", "1")
        _set_path(segments, "RXA.3", cfg.get("administration_date") or now_ts)
        _set_path(segments, "RXA.5", cfg.get("vaccine_code") or "998^No Vaccine Administered^CVX")

        # Apply user-provided defaults before field mappings
        for path, value in default_values.items():
            _set_path(segments, path, value)

        # Apply field mappings from CSV columns
        for hl7_path, source in field_mappings.items():
            val = _get_value(row, source)
            if val is not None:
                _set_path(segments, hl7_path, val)

        hl7_messages.append(_build_message(segments))

    # Update workflow context with the first generated message for downstream steps
    if hl7_messages:
        context.raw_message = hl7_messages[0]
        try:
            context.message = hl7_parser.parse_message(hl7_messages[0])
        except Exception:
            context.message = None
        context.variables["raw_message"] = hl7_messages[0]
        context.variables["hl7_messages"] = hl7_messages

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={
            "message": "CSV converted to HL7",
            "row_count": len(rows),
            "hl7_messages": hl7_messages,
            "sample_message": hl7_messages[0] if hl7_messages else "",
            "mappings_used": field_mappings
        },
        variables={
            "hl7_messages": hl7_messages,
            "raw_message": hl7_messages[0] if hl7_messages else None,
            "csv_row_count": len(rows)
        }
    )


async def process_icare_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Send HL7 VXU messages to an ICARE (immunization registry) endpoint over HTTP/S.

    Config:
      - endpoint_url: required
      - payload_source: "raw_message" (default) or "hl7_messages"
      - send_batch: bool (send all hl7_messages if present)
      - headers: optional dict
      - auth: { type: "basic"|"bearer", username, password, token }
      - timeout_seconds: int (default 30)
      - verify_ssl: bool (default True)
      - dry_run: bool (if true, do not send – just return payload preview)
    """
    cfg = activity.get("config", {}) or {}
    endpoint = cfg.get("endpoint_url") or cfg.get("url")
    if not endpoint:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="ICARE endpoint_url is required"
        )

    payload_source = cfg.get("payload_source") or "raw_message"
    send_batch = bool(cfg.get("send_batch", False))
    timeout = int(cfg.get("timeout_seconds", 30))
    verify_ssl = bool(cfg.get("verify_ssl", True))
    expect_ack = bool(cfg.get("expect_ack", True))
    dry_run = bool(cfg.get("dry_run", False))
    headers = cfg.get("headers", {}) or {}
    headers.setdefault("Content-Type", "application/hl7-v2")
    headers.setdefault("Accept", "application/hl7-v2")

    auth_cfg = cfg.get("auth", {}) or {}
    auth_type = (auth_cfg.get("type") or "none").lower()
    auth = None
    if auth_type == "basic":
        auth = (auth_cfg.get("username", ""), auth_cfg.get("password", ""))
    elif auth_type == "bearer":
        token = auth_cfg.get("token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    # Resolve payload(s)
    messages: List[str] = []
    if payload_source == "hl7_messages":
        msgs = context.variables.get("hl7_messages")
        if isinstance(msgs, list):
            messages = [m for m in msgs if m]
    if not messages and context.raw_message:
        messages = [context.raw_message]
    if not messages and context.variables.get("raw_message"):
        messages = [context.variables.get("raw_message")]

    if not messages:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No HL7 message available to send to ICARE"
        )

    # Dry run: return without sending
    if dry_run:
        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "Dry-run: ICARE send skipped",
                "endpoint_url": endpoint,
                "payload_preview": messages if send_batch else messages[:1],
                "send_batch": send_batch
            },
            variables={"icare_dry_run": True}
        )

    results: List[Dict[str, Any]] = []
    success = True

    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, verify=verify_ssl) as client:
            iterable = messages if send_batch else messages[:1]
            for idx, msg in enumerate(iterable):
                resp = await client.post(endpoint, data=msg.encode("utf-8"), headers=headers, auth=auth)
                body_preview = resp.text[:2000] if resp.text else ""
                entry = {
                    "status_code": resp.status_code,
                    "response_text": body_preview,
                    "request_index": idx,
                }
                results.append(entry)
                if not (200 <= resp.status_code < 300):
                    success = False
                    if not send_batch:
                        break
    except Exception as e:
        logger.error(f"icare_sender failed: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"ICARE send failed: {str(e)}"
        )

    return ActivityResult(
        status=ActivityStatus.COMPLETED if success else ActivityStatus.FAILED,
        output_data={
            "message": "ICARE send completed" if success else "ICARE send encountered errors",
            "endpoint_url": endpoint,
            "results": results,
            "expect_ack": expect_ack,
            "send_batch": send_batch
        },
        variables={
            "icare_status": success,
            "icare_response": results[0] if results else {},
            "icare_results": results
        },
        error_message=None if success else "One or more ICARE requests failed"
    )


async def process_icare_webservice_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Send HL7 to I-CARE via SOAP Web Service (WSDL/WCF-compatible POST).

    Config:
      - wsdl_url or endpoint_url: required SOAP endpoint
      - soap_action: optional SOAPAction header (default empty)
      - username/password: optional BasicAuth
      - payload_source: "raw_message" (default) or "hl7_messages"
      - send_batch: bool (false) to only send first message
      - timeout_seconds: int (default 30)
      - verify_ssl: bool (default True)
      - dry_run: bool to skip network call and return envelope preview
    """
    cfg = activity.get("config", {}) or {}
    endpoint = cfg.get("endpoint_url") or cfg.get("wsdl_url")
    if not endpoint:
        return ActivityResult(status=ActivityStatus.FAILED, error_message="ICARE webservice endpoint_url/wsdl_url is required")

    payload_source = cfg.get("payload_source") or "raw_message"
    send_batch = bool(cfg.get("send_batch", False))
    timeout = int(cfg.get("timeout_seconds", 30))
    verify_ssl = bool(cfg.get("verify_ssl", True))
    soap_action = cfg.get("soap_action") or ""
    dry_run = bool(cfg.get("dry_run", False))
    username = cfg.get("username")
    password = cfg.get("password")

    # Resolve payload(s)
    messages: List[str] = []
    if payload_source == "hl7_messages":
        msgs = context.variables.get("hl7_messages")
        if isinstance(msgs, list):
            messages = [m for m in msgs if m]
    if not messages and context.raw_message:
        messages = [context.raw_message]
    if not messages and context.variables.get("raw_message"):
        messages = [context.variables.get("raw_message")]

    if not messages:
        return ActivityResult(status=ActivityStatus.FAILED, error_message="No HL7 message available to send to I-CARE webservice")

    iterable = messages if send_batch else messages[:1]
    results: List[Dict[str, Any]] = []
    success = True

    def _build_envelope(message: str) -> str:
        # Minimal SOAP 1.1 envelope expected by many IIS endpoints; adjust as needed via config if spec changes.
        return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <SubmitSingleMessage xmlns="http://tempuri.org/">
      <hl7Message>{message}</hl7Message>
    </SubmitSingleMessage>
  </soap:Body>
</soap:Envelope>"""

    if dry_run:
        preview = _build_envelope(iterable[0])
        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "Dry-run: ICARE webservice call skipped",
                "endpoint_url": endpoint,
                "payload_preview": preview[:2000],
                "send_batch": send_batch
            },
            variables={"icare_webservice_dry_run": True}
        )

    try:
        import httpx
        auth = None
        if username or password:
            auth = (username or "", password or "")

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
        }
        if soap_action is not None:
            headers["SOAPAction"] = soap_action

        async with httpx.AsyncClient(timeout=timeout, verify=verify_ssl) as client:
            for idx, msg in enumerate(iterable):
                envelope = _build_envelope(msg)
                resp = await client.post(endpoint, data=envelope.encode("utf-8"), headers=headers, auth=auth)
                body_preview = resp.text[:2000] if resp.text else ""
                entry = {
                    "status_code": resp.status_code,
                    "response_text": body_preview,
                    "request_index": idx,
                }
                results.append(entry)
                if not (200 <= resp.status_code < 300):
                    success = False
                    if not send_batch:
                        break
    except Exception as e:
        logger.error(f"ICARE webservice send failed: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"ICARE webservice send failed: {str(e)}"
        )

    return ActivityResult(
        status=ActivityStatus.COMPLETED if success else ActivityStatus.FAILED,
        output_data={
            "message": "ICARE webservice send completed" if success else "ICARE webservice send encountered errors",
            "endpoint_url": endpoint,
            "results": results,
            "send_batch": send_batch
        },
        variables={
            "icare_webservice_status": success,
            "icare_webservice_response": results[0] if results else {},
            "icare_webservice_results": results
        },
        error_message=None if success else "One or more ICARE webservice requests failed"
    )


async def process_icare_sftp_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Upload HL7 batch file to I-CARE over SFTP/FTP.

    Config:
      - host: required (e.g., ftp.idphnet.com)
      - port: optional (default 22 for sftp, 21 for ftp)
      - username / password: required
      - protocol: "sftp" (default) or "ftp"
      - directory: target directory (default "/Distribution/ICARE HL7 EXCHANGE/")
      - filename_template: supports {date}, {timestamp}, {facility}, {random}
      - facility: used in filename substitution (default "TEST")
      - send_batch: bool (default True) to use hl7_messages array; otherwise raw_message
      - dry_run: bool to skip upload and just return the composed payload
    """
    cfg = activity.get("config", {}) or {}
    host = cfg.get("host")
    username = cfg.get("username")
    password = cfg.get("password")
    protocol = (cfg.get("protocol") or "sftp").lower()
    directory = cfg.get("directory") or "/Distribution/ICARE HL7 EXCHANGE/"
    facility = cfg.get("facility") or "TEST"
    send_batch = bool(cfg.get("send_batch", True))
    dry_run = bool(cfg.get("dry_run", False))
    filename_template = cfg.get("filename_template") or "HL7_{facility}_{date}_{timestamp}.txt"
    allow_unknown_hosts = bool(cfg.get("allow_unknown_hosts", False))

    if not host or not username or not password:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="ICARE SFTP requires host, username, and password"
        )

    # Resolve messages
    messages: List[str] = []
    if send_batch:
        msgs = context.variables.get("hl7_messages")
        if isinstance(msgs, list):
            messages = [m for m in msgs if m]
    if not messages and context.raw_message:
        messages = [context.raw_message]
    if not messages and context.variables.get("raw_message"):
        messages = [context.variables.get("raw_message")]

    if not messages:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="No HL7 message available to upload to I-CARE"
        )

    date_str = datetime.utcnow().strftime("%Y%m%d")
    ts_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    rand_str = uuid.uuid4().hex[:8]
    filename = filename_template
    for k, v in {
        "date": date_str,
        "timestamp": ts_str,
        "facility": facility,
        "random": rand_str,
    }.items():
        filename = filename.replace(f"{{{k}}}", str(v)).replace(f"{{{{{k}}}}}", str(v))
    remote_path = os.path.join(directory.rstrip("/"), filename)

    content = "\r\n".join(msg.strip() for msg in messages if isinstance(msg, str))
    if not content.endswith("\r\n"):
        content = content + "\r\n"

    if dry_run:
        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "Dry-run: ICARE SFTP upload skipped",
                "host": host,
                "path": remote_path,
                "message_count": len(messages),
                "protocol": protocol
            },
            variables={"icare_sftp_dry_run": True, "icare_sftp_path": remote_path}
        )

    bytes_written = len(content.encode("utf-8"))

    try:
        if protocol == "ftp":
            import aioftp
            async with aioftp.Client.context(host, port=cfg.get("port", 21), user=username, password=password) as client:
                try:
                    await client.make_directory(directory)
                except Exception:
                    pass  # likely already exists
                await client.upload_stream(io.BytesIO(content.encode("utf-8")), remote_path)
        else:
            import asyncssh
            port = int(cfg.get("port", 22))
            async with asyncssh.connect(host, port=port, username=username, password=password, known_hosts=None if allow_unknown_hosts else None) as conn:
                async with conn.start_sftp_client() as sftp:
                    try:
                        await sftp.makedirs(directory)
                    except Exception:
                        pass
                    async with sftp.open(remote_path, "w") as f:
                        await f.write(content)
    except Exception as e:
        logger.error(f"ICARE SFTP upload failed: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"ICARE SFTP upload failed: {str(e)}"
        )

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={
            "message": "ICARE SFTP upload completed",
            "host": host,
            "path": remote_path,
            "bytes_written": bytes_written,
            "message_count": len(messages),
            "protocol": protocol
        },
        variables={
            "icare_sftp_path": remote_path,
            "icare_sftp_bytes": bytes_written
        }
    )
