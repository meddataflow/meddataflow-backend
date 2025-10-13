"""
Interoperability activity processors for non-HL7 healthcare standards.
Provides lightweight parsing, transformation, translation, and sender stubs
for standards such as FHIR, DICOM, X12, NCPDP, and clinical document formats.
The processors focus on normalization into workflow variables so downstream
activities can act on a consistent structure.
"""
import base64
import io
import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple
from xml.etree import ElementTree

from models.workflow_models import ActivityResult, ActivityStatus, WorkflowContext
from services.fhir_summary import (
    FHIRParsingError,
    build_fhir_translation_summary,
    extract_common_fhir_values,
    parse_fhir_resource,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helper utilities                                                            #
# --------------------------------------------------------------------------- #

def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.debug("interoperability_processors: failed to parse JSON, using raw text")
    return value


def _resolve_payload_from_context(
    context: WorkflowContext,
    config: Dict[str, Any],
    default_keys: Iterable[str],
) -> Optional[Any]:
    """
    Resolve payload for processing:
    - Prefer explicit input_variable from config.
    - Fallback to known context variables specific to the standard.
    - Lastly fallback to workflow raw_message.
    """
    input_variable = config.get("input_variable") or config.get("payload_variable")

    if input_variable:
        payload = context.variables.get(input_variable)
        if payload:
            return payload

    for key in default_keys:
        if key in context.variables and context.variables[key]:
            return context.variables[key]

    return context.raw_message


def _set_context_variable(context: WorkflowContext, name: Optional[str], value: Any) -> None:
    if not name:
        return
    context.variables[name] = value


def _extract_json_path(data: Any, path: str) -> Any:
    """
    Extract basic dotted path values from dict/list structures.
    Supports array indices via bracket syntax (e.g., name[0].family).
    """
    if data is None or path is None:
        return None

    current = data
    for part in path.split("."):
        if not part:
            continue
        if "[" in part and part.endswith("]"):
            field, _, index_part = part.partition("[")
            index = int(index_part[:-1])
            current = current.get(field) if isinstance(current, dict) else None
            if isinstance(current, list) and 0 <= index < len(current):
                current = current[index]
            else:
                return None
        else:
            current = current.get(part) if isinstance(current, dict) else None
        if current is None:
            return None
    return current


def _apply_simple_rules(
    source: Dict[str, Any],
    rules: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Apply transformation/translation rules. Supports operations:
    - copy: copy value from source path to target path
    - set: set constant value
    - map: map value using key/value dictionary
    """
    result = {}
    for rule in rules or []:
        operation = (rule.get("operation") or "copy").lower()
        target = rule.get("target") or rule.get("target_path")
        if not target:
            continue

        if operation == "set":
            result[target] = rule.get("value")
            continue

        source_path = rule.get("source") or rule.get("source_path")
        value = _extract_json_path(source, source_path) if source_path else None

        if operation == "map":
            mapping = _safe_json_loads(rule.get("mapping")) or rule.get("mapping", {})
            if isinstance(mapping, dict):
                value = mapping.get(value, rule.get("default"))
        elif operation == "copy":
            pass  # already extracted
        else:
            # Unknown operation - fallback to copy
            pass

        if value is None and "default" in rule:
            value = rule["default"]

        result[target] = value

    return result


def _build_result(
    status: ActivityStatus,
    message: str,
    output: Dict[str, Any],
    variables: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> ActivityResult:
    return ActivityResult(
        status=status,
        output_data={"message": message, **output},
        variables=variables or {},
        error_message=error,
    )


# --------------------------------------------------------------------------- #
# FHIR Activities                                                             #
# --------------------------------------------------------------------------- #

async def process_fhir_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    payload = _resolve_payload_from_context(context, config, ["fhir_payload", "fhir_message", "fhir_resource"])

    if not payload:
        return _build_result(
            ActivityStatus.FAILED,
            "FHIR parser failed",
            {},
            error="No FHIR payload available in context"
        )

    try:
        resource = parse_fhir_resource(payload)
    except FHIRParsingError as exc:
        return _build_result(
            ActivityStatus.FAILED,
            "FHIR parser failed",
            {},
            error=str(exc)
        )

    # Support both old-style extraction_rules and new-style variables config (like HL7 parser)
    variable_definitions = config.get("variables", [])
    extraction_rules = _safe_json_loads(config.get("extraction_rules")) or config.get("extraction_rules")

    extracted_values: Dict[str, Any] = {}

    # Process new-style variables (consistent with HL7 parser)
    for var_def in variable_definitions:
        var_name = var_def.get("name")
        var_source = var_def.get("source")  # JSON path like "name[0].family"
        var_default = var_def.get("default", "")

        if var_name and var_source:
            value = _extract_json_path(resource, var_source)
            if value is None:
                value = var_default
            extracted_values[var_name] = value
            context.variables[var_name] = value

    # Process old-style extraction_rules for backward compatibility
    if isinstance(extraction_rules, list):
        for rule in extraction_rules:
            name = rule.get("name")
            path = rule.get("path")
            default = rule.get("default", "")
            if name and path:
                value = _extract_json_path(resource, path)
                if value is None:
                    value = default
                extracted_values[name] = value
                context.variables[name] = value

    # Supply smart defaults if the caller did not define extraction rules
    auto_values = extract_common_fhir_values(resource)
    for key, value in auto_values.items():
        extracted_values.setdefault(key, value)
        context.variables.setdefault(key, value)

    store_as = config.get("store_parsed_as", "fhir_resource")
    _set_context_variable(context, store_as, resource)

    return _build_result(
        ActivityStatus.COMPLETED,
        "FHIR message parsed",
        {
            "resource_type": resource.get("resourceType"),
            "extracted_variables": extracted_values,
            "resource": resource,
        },
        variables={store_as: resource, "fhir_resource": resource, **extracted_values}
    )


async def process_fhir_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    input_resource = _resolve_payload_from_context(context, config, ["fhir_transformed", "fhir_resource"])

    if not input_resource:
        return _build_result(
            ActivityStatus.FAILED,
            "FHIR transformation failed",
            {},
            error="No FHIR resource available for transformation"
        )

    resource = input_resource if isinstance(input_resource, dict) else _safe_json_loads(input_resource)
    if not isinstance(resource, dict):
        return _build_result(
            ActivityStatus.FAILED,
            "FHIR transformation failed",
            {},
            error="Input resource must be a JSON object"
        )

    rules = _safe_json_loads(config.get("transformation_rules")) or config.get("transformation_rules")
    transformed = resource.copy()
    applied = _apply_simple_rules(resource, rules if isinstance(rules, list) else [])
    if applied:
        transformed.setdefault("extension", [])
        transformed["extension"].append({
            "url": "http://mediflow.example/extensions/mapped-fields",
            "valueString": json.dumps(applied)
        })

    output_variable = config.get("output_variable", "fhir_transformed")
    _set_context_variable(context, output_variable, transformed)

    return _build_result(
        ActivityStatus.COMPLETED,
        "FHIR resource transformed",
        {"transformed_resource": transformed, "applied_rules": applied},
        variables={output_variable: transformed, "fhir_applied_rules": applied}
    )


async def process_fhir_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    resource = _resolve_payload_from_context(context, config, ["fhir_transformed", "fhir_resource"])
    resource = resource if isinstance(resource, dict) else _safe_json_loads(resource)

    if isinstance(resource, dict) and "resource" in resource and isinstance(resource["resource"], dict):
        resource_payload = resource["resource"]
    elif (
        isinstance(resource, dict)
        and "parsed_data" in resource
        and isinstance(resource["parsed_data"], dict)
        and "resource" in resource["parsed_data"]
    ):
        resource_payload = resource["parsed_data"]["resource"]
    else:
        resource_payload = resource

    try:
        resource_dict = parse_fhir_resource(resource_payload)
    except FHIRParsingError as exc:
        return _build_result(
            ActivityStatus.FAILED,
            "FHIR translation failed",
            {},
            error=str(exc)
        )

    target_format = (config.get("target_format") or "json").lower()
    translation_profile = config.get("translation_mappings")
    store_as = config.get("store_result_as", f"fhir_{target_format}_payload")

    if target_format == "hl7v2":
        translated: Any = f"MSH|^~\\&|FHIR|FHIR-TRANSLATOR|MEDIFLOW|MEDIFLOW|{datetime.utcnow().strftime('%Y%m%d%H%M%S')}||FHIR^XFR^FHIR|{resource_dict.get('id','UNKNOWN')}|P|2.5.1"
    elif target_format in {"english", "summary", "human"}:
        extracted = extract_common_fhir_values(resource_dict)
        translated = build_fhir_translation_summary(resource_dict, extracted)
    else:
        translated = resource_dict

    _set_context_variable(context, store_as, translated)

    if isinstance(translated, dict):
        output_payload: Dict[str, Any] = {
            "target_format": target_format,
            "translation_profile": translation_profile,
            **translated,
        }
    else:
        output_payload = {
            "target_format": target_format,
            "translation_profile": translation_profile,
            "translated": translated,
        }

    return _build_result(
        ActivityStatus.COMPLETED,
        "FHIR resource translated",
        output_payload,
        variables={store_as: translated}
    )


async def process_fhir_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    payload_variable = config.get("payload_variable", "fhir_transformed")
    payload = context.variables.get(payload_variable) or {}

    endpoint = config.get("endpoint_url")
    transport = (config.get("transport_protocol") or "https").lower()
    simulate = config.get("simulate", True)

    if not endpoint:
        return _build_result(
            ActivityStatus.FAILED,
            "FHIR sender failed",
            {},
            error="FHIR endpoint URL is required"
        )

    if not simulate and transport in {"http", "https"}:
        try:
            import httpx
            response = await httpx.AsyncClient(timeout=30).post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/fhir+json"},
            )
            success = 200 <= response.status_code < 300
            return _build_result(
                ActivityStatus.COMPLETED if success else ActivityStatus.FAILED,
                "FHIR sender executed",
                {"endpoint": endpoint, "status_code": response.status_code, "transport": transport},
                variables={"fhir_sender_status": response.status_code},
                error=None if success else f"HTTP {response.status_code}"
            )
        except Exception as exc:
            return _build_result(
                ActivityStatus.FAILED,
                "FHIR sender failed",
                {"endpoint": endpoint, "transport": transport},
                error=str(exc)
            )

    # Simulation path (default)
    return _build_result(
        ActivityStatus.COMPLETED,
        "FHIR sender simulated",
        {"endpoint": endpoint, "transport": transport, "simulated": True},
        variables={"fhir_sender_status": "SIMULATED"}
    )


# --------------------------------------------------------------------------- #
# DICOM Activities                                                            #
# --------------------------------------------------------------------------- #

def _decode_dicom_payload(payload: Any) -> Tuple[Optional[bytes], Dict[str, Any]]:
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload), {}
    if isinstance(payload, str):
        try:
            binary = base64.b64decode(payload, validate=True)
            return binary, _extract_dicom_metadata(binary)
        except Exception as e:
            logger.debug(f"_decode_dicom_payload: base64 decode failed: {e}, treating as JSON")
            # Assume JSON metadata string
            return None, _safe_json_loads(payload) or {}
    if isinstance(payload, dict):
        return None, payload
    return None, {}


def _extract_dicom_metadata(binary_payload: bytes) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    try:
        try:
            import pydicom  # type: ignore
        except ImportError:
            logger.warning("pydicom not installed, cannot parse DICOM metadata")
            return {"error": "pydicom not installed", "install_command": "pip install pydicom"}

        dataset = pydicom.dcmread(io.BytesIO(binary_payload), stop_before_pixels=True, force=True)

        def _safe_attr(attr: str) -> Optional[str]:
            value = getattr(dataset, attr, None)
            if value is None:
                return None
            try:
                return str(value)
            except Exception:
                return None

        # Core patient demographics
        metadata = {
            "PatientName": _safe_attr("PatientName"),
            "PatientID": _safe_attr("PatientID"),
            "PatientSex": _safe_attr("PatientSex"),
            "PatientBirthDate": _safe_attr("PatientBirthDate"),
            "PatientAge": _safe_attr("PatientAge"),
            "PatientWeight": _safe_attr("PatientWeight"),
            "PatientSize": _safe_attr("PatientSize"),
        }

        # Study information
        study_info = {
            "StudyInstanceUID": _safe_attr("StudyInstanceUID"),
            "StudyID": _safe_attr("StudyID"),
            "StudyDate": _safe_attr("StudyDate"),
            "StudyTime": _safe_attr("StudyTime"),
            "StudyDescription": _safe_attr("StudyDescription"),
            "AccessionNumber": _safe_attr("AccessionNumber"),
        }
        metadata.update({k: v for k, v in study_info.items() if v})

        # Series information
        series_info = {
            "SeriesInstanceUID": _safe_attr("SeriesInstanceUID"),
            "SeriesNumber": _safe_attr("SeriesNumber"),
            "SeriesDescription": _safe_attr("SeriesDescription"),
            "SeriesDate": _safe_attr("SeriesDate"),
            "SeriesTime": _safe_attr("SeriesTime"),
        }
        metadata.update({k: v for k, v in series_info.items() if v})

        # Image information
        image_info = {
            "SOPInstanceUID": _safe_attr("SOPInstanceUID"),
            "SOPClassUID": _safe_attr("SOPClassUID"),
            "InstanceNumber": _safe_attr("InstanceNumber"),
            "ImageType": _safe_attr("ImageType"),
        }
        metadata.update({k: v for k, v in image_info.items() if v})

        # Equipment and acquisition
        equipment_info = {
            "Modality": _safe_attr("Modality"),
            "Manufacturer": _safe_attr("Manufacturer"),
            "ManufacturerModelName": _safe_attr("ManufacturerModelName"),
            "DeviceSerialNumber": _safe_attr("DeviceSerialNumber"),
            "SoftwareVersions": _safe_attr("SoftwareVersions"),
            "StationName": _safe_attr("StationName"),
        }
        metadata.update({k: v for k, v in equipment_info.items() if v})

        # Clinical information
        clinical_info = {
            "BodyPartExamined": _safe_attr("BodyPartExamined"),
            "ViewPosition": _safe_attr("ViewPosition"),
            "PatientPosition": _safe_attr("PatientPosition"),
            "ReferringPhysicianName": _safe_attr("ReferringPhysicianName"),
            "PerformingPhysicianName": _safe_attr("PerformingPhysicianName"),
            "RequestingPhysician": _safe_attr("RequestingPhysician"),
            "InstitutionName": _safe_attr("InstitutionName"),
            "InstitutionAddress": _safe_attr("InstitutionAddress"),
        }
        metadata.update({k: v for k, v in clinical_info.items() if v})

        # Image characteristics
        image_chars = {
            "Rows": _safe_attr("Rows"),
            "Columns": _safe_attr("Columns"),
            "BitsAllocated": _safe_attr("BitsAllocated"),
            "BitsStored": _safe_attr("BitsStored"),
            "PixelSpacing": _safe_attr("PixelSpacing"),
            "SliceThickness": _safe_attr("SliceThickness"),
            "NumberOfFrames": _safe_attr("NumberOfFrames"),
        }
        metadata.update({k: v for k, v in image_chars.items() if v})

        # Contrast and protocol
        protocol_info = {
            "ContrastBolusAgent": _safe_attr("ContrastBolusAgent"),
            "ProtocolName": _safe_attr("ProtocolName"),
            "ScanningSequence": _safe_attr("ScanningSequence"),
            "SequenceVariant": _safe_attr("SequenceVariant"),
        }
        metadata.update({k: v for k, v in protocol_info.items() if v})

        # Additional imaging parameters
        imaging_params = {
            "KVP": _safe_attr("KVP"),
            "ExposureTime": _safe_attr("ExposureTime"),
            "XRayTubeCurrent": _safe_attr("XRayTubeCurrent"),
            "Exposure": _safe_attr("Exposure"),
            "FilterType": _safe_attr("FilterType"),
            "GeneratorPower": _safe_attr("GeneratorPower"),
            "FocalSpots": _safe_attr("FocalSpots"),
            "DateOfLastCalibration": _safe_attr("DateOfLastCalibration"),
            "TimeOfLastCalibration": _safe_attr("TimeOfLastCalibration"),
        }
        metadata.update({k: v for k, v in imaging_params.items() if v})

        # Window/Level information for display
        display_params = {
            "WindowCenter": _safe_attr("WindowCenter"),
            "WindowWidth": _safe_attr("WindowWidth"),
            "RescaleIntercept": _safe_attr("RescaleIntercept"),
            "RescaleSlope": _safe_attr("RescaleSlope"),
            "RescaleType": _safe_attr("RescaleType"),
            "PhotometricInterpretation": _safe_attr("PhotometricInterpretation"),
        }
        metadata.update({k: v for k, v in display_params.items() if v})

        # Anatomical orientation
        orientation_info = {
            "ImageOrientationPatient": _safe_attr("ImageOrientationPatient"),
            "ImagePositionPatient": _safe_attr("ImagePositionPatient"),
            "SliceLocation": _safe_attr("SliceLocation"),
            "ImageLaterality": _safe_attr("ImageLaterality"),
            "Laterality": _safe_attr("Laterality"),
        }
        metadata.update({k: v for k, v in orientation_info.items() if v})

        # Additional study/procedure details
        procedure_info = {
            "ProcedureCodeSequence": _safe_attr("ProcedureCodeSequence"),
            "ReasonForRequestedProcedure": _safe_attr("ReasonForRequestedProcedure"),
            "RequestedProcedureDescription": _safe_attr("RequestedProcedureDescription"),
            "PerformedProcedureStepDescription": _safe_attr("PerformedProcedureStepDescription"),
            "OperatorsName": _safe_attr("OperatorsName"),
            "AdmittingDiagnosesDescription": _safe_attr("AdmittingDiagnosesDescription"),
        }
        metadata.update({k: v for k, v in procedure_info.items() if v})

        # Radiation dose information (important for safety)
        dose_info = {
            "OrganDose": _safe_attr("OrganDose"),
            "ExposureDoseSequence": _safe_attr("ExposureDoseSequence"),
            "CTDIvol": _safe_attr("CTDIvol"),
            "DoseAreaProduct": _safe_attr("DoseAreaProduct"),
            "EstimatedRadiographicMagnificationFactor": _safe_attr("EstimatedRadiographicMagnificationFactor"),
        }
        metadata.update({k: v for k, v in dose_info.items() if v})

        # Image quality and compression
        quality_info = {
            "ImageCompressionRatio": _safe_attr("ImageCompressionRatio"),
            "LossyImageCompression": _safe_attr("LossyImageCompression"),
            "LossyImageCompressionRatio": _safe_attr("LossyImageCompressionRatio"),
            "LossyImageCompressionMethod": _safe_attr("LossyImageCompressionMethod"),
            "TransferSyntaxUID": _safe_attr("TransferSyntaxUID"),
        }
        metadata.update({k: v for k, v in quality_info.items() if v})

        # Remove None values
        metadata = {k: v for k, v in metadata.items() if v}

    except Exception as exc:
        metadata = {"parse_warning": str(exc)}
    return metadata


async def process_dicom_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    payload = _resolve_payload_from_context(context, config, ["dicom_payload", "dicom_file", "dicom_metadata"])
    if not payload:
        return _build_result(
            ActivityStatus.FAILED,
            "DICOM parser failed",
            {},
            error="No DICOM payload available in context"
        )

    binary_payload, metadata = _decode_dicom_payload(payload)

    # Check if there was an error during metadata extraction
    if isinstance(metadata, dict) and metadata.get("error"):
        return _build_result(
            ActivityStatus.FAILED,
            "DICOM parser failed",
            metadata,
            error=metadata.get("error")
        )

    summary = {
        "has_binary": bool(binary_payload),
        "metadata_keys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
        "byte_size": len(binary_payload) if binary_payload else 0,
    }

    # If we got metadata, merge it into the summary
    if metadata:
        summary["metadata"] = metadata

    # Extract variables from DICOM metadata (consistent with HL7 parser)
    variable_definitions = config.get("variables", [])
    extracted_values: Dict[str, Any] = {}

    for var_def in variable_definitions:
        var_name = var_def.get("name")
        var_source = var_def.get("source")  # DICOM tag name like "PatientName", "PatientID"
        var_default = var_def.get("default", "")

        if var_name and var_source:
            # Extract from metadata dict using the tag name as key
            value = metadata.get(var_source) if isinstance(metadata, dict) else None
            if value is None:
                value = var_default
            extracted_values[var_name] = value
            context.variables[var_name] = value

    store_as = config.get("store_parsed_as", "dicom_metadata")
    _set_context_variable(context, store_as, metadata or summary)

    return _build_result(
        ActivityStatus.COMPLETED,
        "DICOM payload parsed",
        {
            **summary,
            "extracted_variables": extracted_values,
        },
        variables={store_as: metadata or summary, "dicom_summary": summary, **extracted_values}
    )


async def process_dicom_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    metadata = _resolve_payload_from_context(context, config, ["dicom_metadata"])
    metadata = metadata if isinstance(metadata, dict) else _safe_json_loads(metadata)

    if not isinstance(metadata, dict):
        return _build_result(
            ActivityStatus.FAILED,
            "DICOM transformation failed",
            {},
            error="DICOM metadata missing or invalid"
        )

    rules = _safe_json_loads(config.get("transformation_rules")) or []
    applied = _apply_simple_rules(metadata, rules if isinstance(rules, list) else [])
    transformed = {**metadata, **applied}

    output_variable = config.get("output_variable", "dicom_transformed_metadata")
    _set_context_variable(context, output_variable, transformed)

    return _build_result(
        ActivityStatus.COMPLETED,
        "DICOM metadata transformed",
        {"transformed_metadata": transformed, "applied_rules": applied},
        variables={output_variable: transformed}
    )


async def process_dicom_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    metadata = _resolve_payload_from_context(context, config, ["dicom_transformed_metadata", "dicom_metadata"])
    metadata = metadata if isinstance(metadata, dict) else _safe_json_loads(metadata)

    if not isinstance(metadata, dict):
        return _build_result(
            ActivityStatus.FAILED,
            "DICOM translation failed",
            {},
            error="DICOM metadata missing or invalid"
        )

    # If metadata is nested (from parser output), extract the actual metadata
    if "metadata" in metadata and isinstance(metadata["metadata"], dict):
        actual_metadata = metadata["metadata"]
    else:
        actual_metadata = metadata

    target_format = (config.get("target_format") or "english").lower()
    store_as = config.get("store_result_as", f"dicom_{target_format}")

    # Generate English translation
    if target_format == "english":
        patient_name = actual_metadata.get("PatientName", "Unknown Patient")
        patient_id = actual_metadata.get("PatientID", "N/A")
        modality = actual_metadata.get("Modality", "Unknown")
        study_date = actual_metadata.get("StudyDate", "Unknown")
        body_part = actual_metadata.get("BodyPartExamined", "Unknown")
        institution = actual_metadata.get("InstitutionName", "Unknown Institution")

        # Format study date if available
        formatted_date = study_date
        if study_date and study_date != "Unknown" and len(study_date) == 8:
            try:
                formatted_date = f"{study_date[0:4]}-{study_date[4:6]}-{study_date[6:8]}"
            except:
                pass

        # Format study time if available
        formatted_time = None
        study_time = actual_metadata.get("StudyTime")
        if study_time and len(study_time) >= 6:
            try:
                formatted_time = f"{study_time[0:2]}:{study_time[2:4]}:{study_time[4:6]}"
            except:
                pass

        # Build human-readable summary with more context
        summary_parts = []

        # Modality-specific descriptions
        modality_descriptions = {
            "CT": "Computed Tomography (CT) scan",
            "MR": "Magnetic Resonance Imaging (MRI) scan",
            "CR": "Computed Radiography (X-Ray)",
            "DX": "Digital Radiography (X-Ray)",
            "US": "Ultrasound examination",
            "MG": "Mammography scan",
            "NM": "Nuclear Medicine study",
            "PT": "Positron Emission Tomography (PET) scan",
            "XA": "X-Ray Angiography",
            "RF": "Radiofluoroscopy procedure",
        }
        modality_desc = modality_descriptions.get(modality, f"{modality} imaging study" if modality != "Unknown" else "Medical imaging study")
        summary_parts.append(modality_desc)

        if patient_name != "Unknown Patient":
            summary_parts.append(f"for patient {patient_name}")

        if body_part != "Unknown":
            summary_parts.append(f"examining {body_part.lower()}")

        if formatted_date != "Unknown":
            date_str = f"performed on {formatted_date}"
            if formatted_time:
                date_str += f" at {formatted_time}"
            summary_parts.append(date_str)

        if institution != "Unknown Institution":
            summary_parts.append(f"at {institution}")

        summary = " ".join(summary_parts)

        # Patient Demographics
        demographics = []
        if patient_id != "N/A":
            demographics.append(f"Patient ID: {patient_id}")
        if actual_metadata.get("PatientSex"):
            sex_map = {"M": "Male", "F": "Female", "O": "Other"}
            demographics.append(f"Sex: {sex_map.get(actual_metadata['PatientSex'], actual_metadata['PatientSex'])}")
        if actual_metadata.get("PatientBirthDate"):
            dob = actual_metadata['PatientBirthDate']
            if len(dob) == 8:
                formatted_dob = f"{dob[0:4]}-{dob[4:6]}-{dob[6:8]}"
                demographics.append(f"Date of Birth: {formatted_dob}")
            else:
                demographics.append(f"Date of Birth: {dob}")
        if actual_metadata.get("PatientAge"):
            demographics.append(f"Age: {actual_metadata['PatientAge']}")
        if actual_metadata.get("PatientWeight"):
            demographics.append(f"Weight: {actual_metadata['PatientWeight']} kg")
        if actual_metadata.get("PatientSize"):
            demographics.append(f"Height: {actual_metadata['PatientSize']} m")

        # Clinical Information
        clinical_details = []
        if actual_metadata.get("StudyDescription"):
            clinical_details.append(f"Study Description: {actual_metadata['StudyDescription']}")
        if actual_metadata.get("SeriesDescription"):
            clinical_details.append(f"Series Description: {actual_metadata['SeriesDescription']}")
        if actual_metadata.get("ReasonForRequestedProcedure"):
            clinical_details.append(f"Reason for Procedure: {actual_metadata['ReasonForRequestedProcedure']}")
        if actual_metadata.get("RequestedProcedureDescription"):
            clinical_details.append(f"Requested Procedure: {actual_metadata['RequestedProcedureDescription']}")
        if actual_metadata.get("PerformedProcedureStepDescription"):
            clinical_details.append(f"Performed Procedure: {actual_metadata['PerformedProcedureStepDescription']}")
        if actual_metadata.get("AdmittingDiagnosesDescription"):
            clinical_details.append(f"Admitting Diagnosis: {actual_metadata['AdmittingDiagnosesDescription']}")
        if actual_metadata.get("ProtocolName"):
            clinical_details.append(f"Protocol: {actual_metadata['ProtocolName']}")

        # Medical Staff
        staff_details = []
        if actual_metadata.get("ReferringPhysicianName"):
            staff_details.append(f"Referring Physician: {actual_metadata['ReferringPhysicianName']}")
        if actual_metadata.get("PerformingPhysicianName"):
            staff_details.append(f"Performing Physician: {actual_metadata['PerformingPhysicianName']}")
        if actual_metadata.get("RequestingPhysician"):
            staff_details.append(f"Requesting Physician: {actual_metadata['RequestingPhysician']}")
        if actual_metadata.get("OperatorsName"):
            staff_details.append(f"Operator: {actual_metadata['OperatorsName']}")

        # Equipment Details
        equipment_details = []
        if actual_metadata.get("Manufacturer"):
            equipment_details.append(f"Manufacturer: {actual_metadata['Manufacturer']}")
        if actual_metadata.get("ManufacturerModelName"):
            equipment_details.append(f"Model: {actual_metadata['ManufacturerModelName']}")
        if actual_metadata.get("StationName"):
            equipment_details.append(f"Station: {actual_metadata['StationName']}")
        if actual_metadata.get("DeviceSerialNumber"):
            equipment_details.append(f"Serial Number: {actual_metadata['DeviceSerialNumber']}")
        if actual_metadata.get("SoftwareVersions"):
            equipment_details.append(f"Software Version: {actual_metadata['SoftwareVersions']}")

        # Image Technical Details
        technical_details = []
        if actual_metadata.get("Rows") and actual_metadata.get("Columns"):
            technical_details.append(f"Image Dimensions: {actual_metadata['Rows']} × {actual_metadata['Columns']} pixels")
        if actual_metadata.get("BitsAllocated"):
            technical_details.append(f"Bit Depth: {actual_metadata['BitsAllocated']} bits")
        if actual_metadata.get("PixelSpacing"):
            technical_details.append(f"Pixel Spacing: {actual_metadata['PixelSpacing']} mm")
        if actual_metadata.get("SliceThickness"):
            technical_details.append(f"Slice Thickness: {actual_metadata['SliceThickness']} mm")
        if actual_metadata.get("NumberOfFrames"):
            technical_details.append(f"Number of Frames: {actual_metadata['NumberOfFrames']}")
        if actual_metadata.get("ViewPosition"):
            technical_details.append(f"View Position: {actual_metadata['ViewPosition']}")
        if actual_metadata.get("PatientPosition"):
            technical_details.append(f"Patient Position: {actual_metadata['PatientPosition']}")
        if actual_metadata.get("Laterality"):
            laterality_map = {"L": "Left", "R": "Right", "B": "Bilateral", "U": "Unpaired"}
            technical_details.append(f"Laterality: {laterality_map.get(actual_metadata['Laterality'], actual_metadata['Laterality'])}")

        # Imaging Parameters
        imaging_details = []
        if actual_metadata.get("KVP"):
            imaging_details.append(f"KVP (Tube Voltage): {actual_metadata['KVP']} kV")
        if actual_metadata.get("XRayTubeCurrent"):
            imaging_details.append(f"Tube Current: {actual_metadata['XRayTubeCurrent']} mA")
        if actual_metadata.get("ExposureTime"):
            imaging_details.append(f"Exposure Time: {actual_metadata['ExposureTime']} ms")
        if actual_metadata.get("Exposure"):
            imaging_details.append(f"Exposure: {actual_metadata['Exposure']} mAs")
        if actual_metadata.get("FilterType"):
            imaging_details.append(f"Filter Type: {actual_metadata['FilterType']}")
        if actual_metadata.get("ContrastBolusAgent"):
            imaging_details.append(f"Contrast Agent: {actual_metadata['ContrastBolusAgent']}")

        # Radiation Dose (Safety Information)
        dose_details = []
        if actual_metadata.get("CTDIvol"):
            dose_details.append(f"CT Dose Index (CTDIvol): {actual_metadata['CTDIvol']} mGy")
        if actual_metadata.get("DoseAreaProduct"):
            dose_details.append(f"Dose Area Product: {actual_metadata['DoseAreaProduct']} dGy·cm²")
        if actual_metadata.get("OrganDose"):
            dose_details.append(f"Organ Dose: {actual_metadata['OrganDose']}")

        # Administrative Details
        admin_details = []
        if actual_metadata.get("AccessionNumber"):
            admin_details.append(f"Accession Number: {actual_metadata['AccessionNumber']}")
        if actual_metadata.get("StudyID"):
            admin_details.append(f"Study ID: {actual_metadata['StudyID']}")
        if actual_metadata.get("SeriesNumber"):
            admin_details.append(f"Series Number: {actual_metadata['SeriesNumber']}")
        if actual_metadata.get("InstanceNumber"):
            admin_details.append(f"Instance Number: {actual_metadata['InstanceNumber']}")

        # Combine all details
        details = demographics + clinical_details + staff_details + equipment_details + technical_details + imaging_details + dose_details + admin_details

        translated = {
            "summary": summary,
            "details": details,
            "modality": modality,
            "patient_name": patient_name,
            "study_date": formatted_date,
            "body_part": body_part,
            "institution": institution,
            "equipment": {
                "manufacturer": actual_metadata.get("Manufacturer"),
                "model": actual_metadata.get("ManufacturerModelName"),
                "station": actual_metadata.get("StationName"),
            },
            "image_info": {
                "rows": actual_metadata.get("Rows"),
                "columns": actual_metadata.get("Columns"),
                "frames": actual_metadata.get("NumberOfFrames"),
                "bits_allocated": actual_metadata.get("BitsAllocated"),
            },
            "raw_metadata": actual_metadata
        }
    else:
        # Other formats (FHIR ImagingStudy, etc.)
        translated = {
            "resourceType": "ImagingStudy" if target_format == "imagingstudy" else "DiagnosticReport",
            "identifier": metadata.get("SOPInstanceUID") or metadata.get("StudyInstanceUID"),
            "issued": metadata.get("AcquisitionDateTime") or datetime.utcnow().isoformat(),
            "seriesCount": metadata.get("SeriesCount"),
            "metadata": metadata,
            "target_format": target_format,
        }

    _set_context_variable(context, store_as, translated)

    return _build_result(
        ActivityStatus.COMPLETED,
        "DICOM metadata translated",
        translated,
        variables={store_as: translated}
    )


async def process_dicom_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    endpoint = config.get("endpoint_url")
    if not endpoint:
        return _build_result(
            ActivityStatus.FAILED,
            "DICOM sender failed",
            {},
            error="Destination endpoint required"
        )

    store_variable = config.get("payload_variable", "dicom_payload")
    payload = context.variables.get(store_variable)

    simulate = config.get("simulate", True)
    if not simulate:
        # Real network transmission would require DICOM networking libs (not available).
        logger.warning("DICOM sender: real transmission not supported in this environment; falling back to simulation")

    return _build_result(
        ActivityStatus.COMPLETED,
        "DICOM sender simulated",
        {"endpoint": endpoint, "transport": config.get("transport_protocol", "dicom"), "simulated": True},
        variables={"dicom_sender_status": "SIMULATED", "dicom_sender_payload_present": payload is not None}
    )


# --------------------------------------------------------------------------- #
# NCPDP Activities                                                            #
# --------------------------------------------------------------------------- #

def _parse_delimited_pairs(payload: str, separators: Tuple[str, ...]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    parts = [payload]
    for sep in separators:
        tokens = []
        for part in parts:
            tokens.extend(part.split(sep))
        parts = tokens

    for token in parts:
        if "=" in token:
            key, value = token.split("=", 1)
            result[key.strip()] = value.strip()
    return result


async def process_ncpdp_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    payload = _resolve_payload_from_context(context, config, ["ncpdp_payload", "ncpdp_message"])
    if not payload:
        return _build_result(
            ActivityStatus.FAILED,
            "NCPDP parser failed",
            {},
            error="No NCPDP payload available"
        )

    payload_str = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
    parsed = _parse_delimited_pairs(payload_str, ("\r", "\n", "|", "\u001D", "\u001E"))

    # Extract variables from NCPDP parsed fields (consistent with HL7 parser)
    variable_definitions = config.get("variables", [])
    extracted_values: Dict[str, Any] = {}

    for var_def in variable_definitions:
        var_name = var_def.get("name")
        var_source = var_def.get("source")  # NCPDP field key
        var_default = var_def.get("default", "")

        if var_name and var_source:
            # Extract from parsed dict using the field key
            value = parsed.get(var_source, var_default)
            extracted_values[var_name] = value
            context.variables[var_name] = value

    store_as = config.get("store_parsed_as", "ncpdp_message")
    _set_context_variable(context, store_as, parsed)

    return _build_result(
        ActivityStatus.COMPLETED,
        "NCPDP payload parsed",
        {
            "parsed_fields": parsed,
            "field_count": len(parsed),
            "extracted_variables": extracted_values,
        },
        variables={store_as: parsed, **extracted_values}
    )


async def process_ncpdp_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    message = _resolve_payload_from_context(context, config, ["ncpdp_message"])
    message = message if isinstance(message, dict) else _safe_json_loads(message)

    if not isinstance(message, dict):
        return _build_result(
            ActivityStatus.FAILED,
            "NCPDP transformation failed",
            {},
            error="NCPDP message missing or invalid"
        )

    rules = _safe_json_loads(config.get("transformation_rules")) or []
    transformed = {**message, **_apply_simple_rules(message, rules if isinstance(rules, list) else [])}
    output_variable = config.get("output_variable", "ncpdp_transformed")
    _set_context_variable(context, output_variable, transformed)

    return _build_result(
        ActivityStatus.COMPLETED,
        "NCPDP message transformed",
        {"transformed_message": transformed},
        variables={output_variable: transformed}
    )


async def process_ncpdp_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    message = _resolve_payload_from_context(context, config, ["ncpdp_transformed", "ncpdp_message"])
    message = message if isinstance(message, dict) else _safe_json_loads(message)
    if not isinstance(message, dict):
        return _build_result(
            ActivityStatus.FAILED,
            "NCPDP translation failed",
            {},
            error="NCPDP message missing or invalid"
        )

    target_format = (config.get("target_format") or "json").lower()
    store_as = config.get("store_result_as", f"ncpdp_{target_format}")
    translated = message if target_format == "json" else json.dumps(message)
    _set_context_variable(context, store_as, translated)

    return _build_result(
        ActivityStatus.COMPLETED,
        "NCPDP message translated",
        {"target_format": target_format},
        variables={store_as: translated}
    )


async def process_ncpdp_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    endpoint = config.get("endpoint_url")
    if not endpoint:
        return _build_result(
            ActivityStatus.FAILED,
            "NCPDP sender failed",
            {},
            error="Destination endpoint required"
        )

    payload_variable = config.get("payload_variable", "ncpdp_transformed")
    payload = context.variables.get(payload_variable)

    return _build_result(
        ActivityStatus.COMPLETED,
        "NCPDP sender simulated",
        {"endpoint": endpoint, "transport": config.get("transport_protocol", "tcp"), "simulated": True},
        variables={"ncpdp_sender_status": "SIMULATED", "ncpdp_payload_present": payload is not None}
    )


# --------------------------------------------------------------------------- #
# X12 Activities                                                              #
# --------------------------------------------------------------------------- #

def _parse_x12_segments(payload: str) -> Dict[str, Any]:
    """
    Parse X12 message into segments.
    Handles both standard (~) and newline-separated formats.
    The segment terminator is defined in ISA position 106 (ISA16).
    """
    # Clean up the payload
    payload = payload.strip()

    # Determine the segment terminator
    # Standard X12 uses ~ but some systems use newline
    if "~" in payload:
        # Standard format with ~ terminators
        segments = payload.split("~")
    else:
        # Newline-separated format
        segments = payload.split("\n")

    parsed: Dict[str, Any] = {}
    for segment in segments:
        # Clean whitespace and skip empty segments
        segment = segment.strip()
        if not segment:
            continue

        # Split on * (element separator)
        elements = segment.split("*")
        if len(elements) == 0:
            continue

        tag = elements[0].strip()
        if not tag:
            continue

        # Store elements (excluding the tag itself)
        parsed.setdefault(tag, []).append(elements[1:])

    return parsed


def _extract_x12_value(parsed_segments: Dict[str, Any], path: str, default: str = "") -> Any:
    """
    Extract value from parsed X12 segments using a path notation.
    Path format: "SEGMENT_TAG[index].element_index"
    Examples:
        - "ST[0].0" -> First ST segment, element 0 (transaction type)
        - "ISA[0].5" -> First ISA segment, element 5 (sender ID)
        - "BEG[0].2" -> First BEG segment, element 2 (PO number)
        - "N1[0].1" -> First N1 segment, element 1 (party name)
    """
    try:
        # Parse the path
        if "[" not in path:
            # Simple tag reference - get first occurrence
            segment_tag = path
            segment_index = 0
            element_index = None
        elif "." in path:
            # Full path with element index
            segment_part, element_part = path.split(".", 1)
            if "[" in segment_part:
                segment_tag = segment_part.split("[")[0]
                segment_index = int(segment_part.split("[")[1].rstrip("]"))
            else:
                segment_tag = segment_part
                segment_index = 0
            element_index = int(element_part)
        else:
            # Just segment with index, no element
            segment_tag = path.split("[")[0]
            segment_index = int(path.split("[")[1].rstrip("]"))
            element_index = None

        # Get the segment
        if segment_tag not in parsed_segments:
            return default

        segments_list = parsed_segments[segment_tag]
        if segment_index >= len(segments_list):
            return default

        segment_elements = segments_list[segment_index]

        # If no element index specified, return the whole segment as joined string
        if element_index is None:
            return "*".join(segment_elements) if segment_elements else default

        # Get specific element
        if element_index >= len(segment_elements):
            return default

        value = segment_elements[element_index]
        return value.strip() if value else default

    except (ValueError, IndexError, KeyError) as e:
        logger.debug(f"Error extracting X12 value from path '{path}': {e}")
        return default


async def process_x12_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    payload = _resolve_payload_from_context(context, config, ["x12_payload", "x12_message"])
    if not payload:
        return _build_result(
            ActivityStatus.FAILED,
            "X12 parser failed",
            {},
            error="No X12 payload available"
        )

    payload_str = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
    parsed = _parse_x12_segments(payload_str)

    # Extract variables from X12 segments (consistent with HL7 parser)
    variable_definitions = config.get("variables", [])
    extracted_values: Dict[str, Any] = {}

    for var_def in variable_definitions:
        var_name = var_def.get("name")
        var_source = var_def.get("source")  # X12 path like "ST[0].0", "ISA[0].5"
        var_default = var_def.get("default", "")

        if var_name and var_source:
            value = _extract_x12_value(parsed, var_source, var_default)
            extracted_values[var_name] = value
            context.variables[var_name] = value

    store_as = config.get("store_parsed_as", "x12_message")
    _set_context_variable(context, store_as, parsed)

    return _build_result(
        ActivityStatus.COMPLETED,
        "X12 payload parsed",
        {
            "segment_count": len(parsed),
            "segments": parsed,
            "extracted_variables": extracted_values,
        },
        variables={store_as: parsed, **extracted_values}
    )


async def process_x12_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    message = _resolve_payload_from_context(context, config, ["x12_message"])
    message = message if isinstance(message, dict) else _safe_json_loads(message)
    if not isinstance(message, dict):
        return _build_result(
            ActivityStatus.FAILED,
            "X12 transformation failed",
            {},
            error="X12 message missing or invalid"
        )

    rules = _safe_json_loads(config.get("transformation_rules")) or []
    transformed = {**message, **_apply_simple_rules(message, rules if isinstance(rules, list) else [])}
    output_variable = config.get("output_variable", "x12_transformed")
    _set_context_variable(context, output_variable, transformed)

    return _build_result(
        ActivityStatus.COMPLETED,
        "X12 message transformed",
        {"transformed_message": transformed},
        variables={output_variable: transformed}
    )


async def process_x12_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    message = _resolve_payload_from_context(context, config, ["x12_transformed", "x12_message"])
    message = message if isinstance(message, dict) else _safe_json_loads(message)

    if not isinstance(message, dict):
        return _build_result(
            ActivityStatus.FAILED,
            "X12 translation failed",
            {},
            error="X12 message missing or invalid"
        )

    target_format = (config.get("target_format") or "json").lower()
    store_as = config.get("store_result_as", f"x12_{target_format}")

    if target_format == "english":
        translated = _translate_x12_to_english(message)
    elif target_format == "json":
        translated = message
    else:
        translated = json.dumps(message)

    _set_context_variable(context, store_as, translated)

    return _build_result(
        ActivityStatus.COMPLETED,
        "X12 message translated",
        {"target_format": target_format, **translated} if target_format == "english" else {"target_format": target_format},
        variables={store_as: translated}
    )


def _translate_x12_to_english(message: Dict[str, Any]) -> Dict[str, Any]:
    """Translate X12 parsed segments into human-readable English"""

    # Determine transaction type from ST segment
    transaction_type = "Unknown Transaction"
    transaction_code = ""
    if "ST" in message and len(message["ST"]) > 0 and len(message["ST"][0]) > 0:
        transaction_code = message["ST"][0][0]
        transaction_types = {
            "810": "Invoice",
            "850": "Purchase Order",
            "855": "Purchase Order Acknowledgment",
            "856": "Advance Ship Notice",
            "997": "Functional Acknowledgment",
            "270": "Eligibility Inquiry",
            "271": "Eligibility Response",
            "837": "Healthcare Claim",
            "835": "Healthcare Claim Payment/Remittance Advice",
            "834": "Benefit Enrollment and Maintenance",
        }
        transaction_type = transaction_types.get(transaction_code, f"Transaction {transaction_code}")

    # Extract ISA (Interchange Control Header)
    isa_info = {}
    if "ISA" in message and len(message["ISA"]) > 0:
        isa = message["ISA"][0]
        if len(isa) >= 13:
            isa_info = {
                "sender_id": isa[5].strip() if len(isa) > 5 else "",
                "receiver_id": isa[7].strip() if len(isa) > 7 else "",
                "date": isa[8] if len(isa) > 8 else "",
                "time": isa[9] if len(isa) > 9 else "",
                "control_number": isa[12] if len(isa) > 12 else ""
            }

    # Extract GS (Functional Group Header)
    gs_info = {}
    if "GS" in message and len(message["GS"]) > 0:
        gs = message["GS"][0]
        if len(gs) >= 6:
            gs_info = {
                "sender": gs[1].strip() if len(gs) > 1 else "",
                "receiver": gs[2].strip() if len(gs) > 2 else "",
                "date": gs[3] if len(gs) > 3 else "",
                "time": gs[4] if len(gs) > 4 else "",
                "control_number": gs[5] if len(gs) > 5 else ""
            }

    # Build summary
    summary_parts = [transaction_type]
    if isa_info.get("sender_id"):
        summary_parts.append(f"from {isa_info['sender_id']}")
    if isa_info.get("receiver_id"):
        summary_parts.append(f"to {isa_info['receiver_id']}")

    summary = " ".join(summary_parts)

    # Parse specific transaction types
    details = []
    line_items = []

    if transaction_code == "850":  # Purchase Order
        # BEG - Beginning Segment for Purchase Order
        if "BEG" in message and len(message["BEG"]) > 0:
            beg = message["BEG"][0]
            purpose_codes = {
                "00": "Original",
                "01": "Cancellation",
                "04": "Change",
                "05": "Replace"
            }
            purpose = purpose_codes.get(beg[0] if len(beg) > 0 else "", beg[0] if len(beg) > 0 else "")
            po_type_codes = {
                "SA": "Stand-alone Order",
                "KN": "Purchase Order",
                "NE": "New Order"
            }
            po_type = po_type_codes.get(beg[1] if len(beg) > 1 else "", beg[1] if len(beg) > 1 else "")
            po_number = beg[2] if len(beg) > 2 else ""
            po_date = beg[4] if len(beg) > 4 else ""

            details.append(f"Purchase Order Type: {purpose} {po_type}")
            if po_number:
                details.append(f"PO Number: {po_number}")
            if po_date and len(po_date) == 8:
                formatted_date = f"{po_date[0:4]}-{po_date[4:6]}-{po_date[6:8]}"
                details.append(f"PO Date: {formatted_date}")

        # REF - Reference Information
        if "REF" in message:
            for ref in message["REF"]:
                if len(ref) >= 2:
                    ref_codes = {
                        "DP": "Department Number",
                        "PS": "Purchase String",
                        "PO": "Purchase Order Number",
                        "IV": "Invoice Number"
                    }
                    ref_type = ref_codes.get(ref[0], ref[0])
                    ref_value = ref[1] if len(ref) > 1 and ref[1] else "Not specified"
                    details.append(f"{ref_type}: {ref_value}")

        # ITD - Terms of Sale/Deferred Terms of Sale
        if "ITD" in message and len(message["ITD"]) > 0:
            itd = message["ITD"][0]
            if len(itd) >= 3:
                term_types = {"14": "Net", "01": "Basic"}
                term_type = term_types.get(itd[0] if len(itd) > 0 else "", "")
                days = itd[2] if len(itd) > 2 else ""
                discount = itd[4] if len(itd) > 4 else ""
                if days:
                    details.append(f"Payment Terms: {term_type} {days} days")
                if discount:
                    details.append(f"Discount: {discount}%")

        # DTM - Date/Time Reference
        if "DTM" in message:
            for dtm in message["DTM"]:
                if len(dtm) >= 2:
                    date_codes = {
                        "001": "Cancel After",
                        "002": "Delivery Requested",
                        "010": "Ship Not Before",
                        "063": "Do Not Deliver After"
                    }
                    date_type = date_codes.get(dtm[0], f"Date ({dtm[0]})")
                    date_val = dtm[1] if len(dtm) > 1 else ""
                    if date_val and len(date_val) == 8:
                        formatted = f"{date_val[0:4]}-{date_val[4:6]}-{date_val[6:8]}"
                        details.append(f"{date_type}: {formatted}")

        # N1 - Party Identification
        parties = {}
        if "N1" in message:
            for idx, n1 in enumerate(message["N1"]):
                if len(n1) >= 1:
                    party_codes = {
                        "ST": "Ship To",
                        "BT": "Bill To",
                        "BY": "Buyer",
                        "SE": "Seller",
                        "VN": "Vendor"
                    }
                    party_type = party_codes.get(n1[0], n1[0])
                    party_name = n1[1] if len(n1) > 1 else ""
                    party_id = n1[3] if len(n1) > 3 else ""

                    # Get address from N3 and city/state from N4
                    address = ""
                    city_state = ""
                    if "N3" in message and len(message["N3"]) > idx:
                        n3 = message["N3"][idx]
                        address = n3[0] if len(n3) > 0 else ""
                    if "N4" in message and len(message["N4"]) > idx:
                        n4 = message["N4"][idx]
                        city = n4[0] if len(n4) > 0 else ""
                        state = n4[1] if len(n4) > 1 else ""
                        zip_code = n4[2] if len(n4) > 2 else ""
                        city_state = f"{city}, {state} {zip_code}".strip()

                    party_info = f"{party_type}: {party_name}"
                    if party_id:
                        party_info += f" (ID: {party_id})"
                    if address:
                        party_info += f", {address}"
                    if city_state:
                        party_info += f", {city_state}"

                    parties[party_type] = party_info
                    details.append(party_info)

        # PO1 - Baseline Item Data (Line Items)
        if "PO1" in message:
            for idx, po1 in enumerate(message["PO1"], 1):
                if len(po1) >= 3:
                    line_num = po1[0] if len(po1) > 0 else str(idx)
                    quantity = po1[1] if len(po1) > 1 else ""
                    unit = po1[2] if len(po1) > 2 else ""
                    price = po1[3] if len(po1) > 3 else ""
                    product_id = po1[6] if len(po1) > 6 else ""
                    vendor_part = po1[9] if len(po1) > 9 else ""

                    unit_names = {
                        "EA": "each",
                        "CA": "case",
                        "BX": "box",
                        "DZ": "dozen",
                        "LB": "pound",
                        "KG": "kilogram"
                    }
                    unit_name = unit_names.get(unit, unit)

                    line_item = {
                        "line_number": line_num,
                        "quantity": quantity,
                        "unit": unit_name,
                        "price": price,
                        "product_id": product_id,
                        "vendor_part": vendor_part,
                        "description": f"Line {line_num}: {quantity} {unit_name} at ${price} each"
                    }

                    if product_id:
                        line_item["description"] += f" - Product: {product_id}"
                    if vendor_part:
                        line_item["description"] += f" (Vendor Part: {vendor_part})"

                    # Calculate line total
                    try:
                        total = float(quantity) * float(price)
                        line_item["line_total"] = f"${total:,.2f}"
                        line_item["description"] += f" = ${total:,.2f}"
                    except (ValueError, TypeError):
                        pass

                    line_items.append(line_item)

        # CTT - Transaction Totals
        if "CTT" in message and len(message["CTT"]) > 0:
            ctt = message["CTT"][0]
            if len(ctt) >= 1:
                line_count = ctt[0] if len(ctt) > 0 else ""
                details.append(f"Total Line Items: {line_count}")

    elif transaction_code in ["837", "835", "270", "271", "834"]:
        # Healthcare transactions - add basic parsing
        details.append(f"Healthcare transaction {transaction_code}")
        details.append("Detailed parsing available for 850 (Purchase Orders)")

    # Build result
    result = {
        "summary": summary,
        "transaction_type": transaction_type,
        "transaction_code": transaction_code,
        "details": details,
        "line_items": line_items,
        "interchange_info": isa_info,
        "group_info": gs_info,
        "raw_segments": message
    }

    return result


async def process_x12_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    endpoint = config.get("endpoint_url")
    if not endpoint:
        return _build_result(
            ActivityStatus.FAILED,
            "X12 sender failed",
            {},
            error="Destination endpoint required"
        )

    payload_variable = config.get("payload_variable", "x12_transformed")
    payload = context.variables.get(payload_variable)

    return _build_result(
        ActivityStatus.COMPLETED,
        "X12 sender simulated",
        {"endpoint": endpoint, "transport": config.get("transport_protocol", "sftp"), "simulated": True},
        variables={"x12_sender_status": "SIMULATED", "x12_payload_present": payload is not None}
    )


# --------------------------------------------------------------------------- #
# Clinical Document Activities (CDA/CCD/CCR)                                  #
# --------------------------------------------------------------------------- #

def _parse_xml_document(payload: str) -> ElementTree.Element:
    return ElementTree.fromstring(payload)


def _extract_cda_summary(root: ElementTree.Element) -> Dict[str, Any]:
    ns = {"cda": "urn:hl7-org:v3"}

    def _text(element: Optional[ElementTree.Element]) -> Optional[str]:
        if element is None:
            return None
        text = "".join(element.itertext()).strip()
        return text or None

    def _find_text(path: str) -> Optional[str]:
        element = root.find(path, namespaces=ns)
        return _text(element)

    def _find_attr(path: str, attr: str) -> Optional[str]:
        element = root.find(path, namespaces=ns)
        return element.get(attr) if element is not None else None

    def _format_address(address_element: Optional[ElementTree.Element]) -> Optional[str]:
        if not address_element:
            return None
        parts = [(_text(child) or "").strip() for child in address_element if _text(child)]
        return ", ".join([part for part in parts if part])

    def _collect_sections() -> list:
        sections = []
        for section in root.findall(".//cda:component/cda:structuredBody/cda:component/cda:section", namespaces=ns):
            section_text = section.find("cda:text", namespaces=ns)
            code_element = section.find("cda:code", namespaces=ns)
            sections.append({
                "title": _text(section.find("cda:title", namespaces=ns)),
                "code": code_element.get("code") if code_element is not None else None,
                "code_system": code_element.get("codeSystem") if code_element is not None else None,
                "code_display_name": code_element.get("displayName") if code_element is not None else None,
                "text": _text(section_text)
            })
        return [s for s in sections if any(s.values())]

    effective_time_elem = root.find(".//cda:effectiveTime", namespaces=ns)
    patient_role = root.find(".//cda:recordTarget/cda:patientRole", namespaces=ns)
    patient = patient_role.find("cda:patient", namespaces=ns) if patient_role is not None else None
    author_elements = root.findall(".//cda:author", namespaces=ns)

    patient_summary = None
    if patient_role is not None or patient is not None:
        given = _find_text(".//cda:recordTarget/cda:patientRole/cda:patient/cda:name/cda:given")
        family = _find_text(".//cda:recordTarget/cda:patientRole/cda:patient/cda:name/cda:family")
        full_name = " ".join(part for part in [given, family] if part).strip() or None
        patient_summary = {
            "id": _find_attr(".//cda:recordTarget/cda:patientRole/cda:id", "extension"),
            "full_name": full_name,
            "given_name": given,
            "family_name": family,
            "gender": _find_attr(".//cda:recordTarget/cda:patientRole/cda:patient/cda:administrativeGenderCode", "code"),
            "birth_time": _find_attr(".//cda:recordTarget/cda:patientRole/cda:patient/cda:birthTime", "value"),
            "telecom": _find_attr(".//cda:recordTarget/cda:patientRole/cda:telecom", "value"),
            "address": _format_address(patient_role.find("cda:addr", namespaces=ns)) if patient_role is not None else None
        }

    authors = []
    for author in author_elements:
        assigned = author.find("cda:assignedAuthor", namespaces=ns)
        if assigned is None:
            continue
        person_name = _text(assigned.find("cda:assignedPerson/cda:name", namespaces=ns))
        organization_name = _text(assigned.find("cda:representedOrganization/cda:name", namespaces=ns))
        time_element = author.find("cda:time", namespaces=ns)
        authors.append({
            "time": time_element.get("value") if time_element is not None else None,
            "id": assigned.find("cda:id", namespaces=ns).get("extension") if assigned.find("cda:id", namespaces=ns) is not None else None,
            "name": person_name,
            "organization": organization_name,
        })

    custodian_org = root.find(".//cda:custodian/cda:assignedCustodian/cda:representedCustodianOrganization", namespaces=ns)

    summary = {
        "title": _find_text(".//cda:title"),
        "effective_time": effective_time_elem.get("value") if effective_time_elem is not None else None,
        "document_code": {
            "code": _find_attr(".//cda:code", "code"),
            "code_system": _find_attr(".//cda:code", "codeSystem"),
            "display_name": _find_attr(".//cda:code", "displayName"),
        },
        "confidentiality_code": _find_attr(".//cda:confidentialityCode", "code"),
        "language_code": _find_attr(".//cda:languageCode", "code"),
        "patient": patient_summary,
        "authors": [author for author in authors if any(author.values())],
        "custodian": {
            "id": custodian_org.find("cda:id", namespaces=ns).get("root") if custodian_org is not None and custodian_org.find("cda:id", namespaces=ns) is not None else None,
            "name": _text(custodian_org.find("cda:name", namespaces=ns)) if custodian_org is not None else None
        },
        "sections": _collect_sections(),
    }
    return summary


def _extract_ccr_summary(root: ElementTree.Element) -> Dict[str, Any]:
    ns = {"ccr": "urn:astm-org:CCR"}

    def _text(element: Optional[ElementTree.Element]) -> Optional[str]:
        if element is None:
            return None
        text = "".join(element.itertext()).strip()
        return text or None

    def _find_text(path: str) -> Optional[str]:
        element = root.find(path, namespaces=ns)
        return _text(element)

    actors: Dict[str, Dict[str, Any]] = {}
    for actor in root.findall(".//ccr:Actors/ccr:Actor", namespaces=ns):
        actor_id = _text(actor.find("ccr:ActorObjectID", namespaces=ns))
        if not actor_id:
            continue
        entry: Dict[str, Any] = {}
        person = actor.find("ccr:Person", namespaces=ns)
        if person is not None:
            name = person.find("ccr:Name/ccr:CurrentName", namespaces=ns)
            if name is not None:
                given = _text(name.find("ccr:Given", namespaces=ns))
                family = _text(name.find("ccr:Family", namespaces=ns))
                entry["name"] = " ".join(part for part in [given, family] if part)
        organization_name = _text(actor.find("ccr:Organization/ccr:Name", namespaces=ns))
        if organization_name:
            entry["organization"] = organization_name
        if entry:
            actors[actor_id] = entry

    patient = {}
    patient_elem = root.find("ccr:Patient", namespaces=ns)
    if patient_elem is not None:
        actor_id = _text(patient_elem.find("ccr:ActorID", namespaces=ns))
        actor_info = actors.get(actor_id, {})
        person = patient_elem.find("ccr:Person", namespaces=ns)
        given = _text(person.find("ccr:Name/ccr:CurrentName/ccr:Given", namespaces=ns)) if person is not None else None
        family = _text(person.find("ccr:Name/ccr:CurrentName/ccr:Family", namespaces=ns)) if person is not None else None
        full_name = actor_info.get("name") or " ".join(part for part in [given, family] if part)
        gender = _text(person.find("ccr:Gender/ccr:Text", namespaces=ns)) if person is not None else None
        birth_time = _text(person.find("ccr:DateOfBirth/ccr:ExactDateTime", namespaces=ns)) if person is not None else None
        patient = {
            "id": actor_id,
            "full_name": full_name,
            "given_name": given,
            "family_name": family,
            "gender": gender,
            "birth_time": birth_time,
        }

    from_actor_id = _text(root.find("ccr:From/ccr:ActorID", namespaces=ns))
    authors = []
    if from_actor_id and from_actor_id in actors:
        authors.append({
            "name": actors[from_actor_id].get("name"),
            "organization": actors[from_actor_id].get("organization"),
        })
    for actor_id, info in actors.items():
        if actor_id in {from_actor_id, patient.get("id")}:
            continue
        if info.get("name") or info.get("organization"):
            authors.append({"name": info.get("name"), "organization": info.get("organization")})

    sections = []

    def _collect_section_items(path: str, title: str) -> None:
        entries = []
        for item in root.findall(path, namespaces=ns):
            description = _text(item.find("ccr:Description/ccr:Text", namespaces=ns))
            if description:
                entries.append(description)
        if entries:
            sections.append({"title": title, "text": "; ".join(entries)})

    _collect_section_items(".//ccr:Body/ccr:Problems/ccr:Problem", "Problems")
    _collect_section_items(".//ccr:Body/ccr:Alerts/ccr:Alert", "Alerts")

    meds = []
    for med in root.findall(".//ccr:Body/ccr:Medications/ccr:Medication", namespaces=ns):
        name = _text(med.find("ccr:Product/ccr:ProductName/ccr:Text", namespaces=ns))
        direction = _text(med.find("ccr:Directions/ccr:Direction/ccr:Text", namespaces=ns))
        status = _text(med.find("ccr:Status/ccr:Text", namespaces=ns))
        parts = [part for part in [name, direction, status] if part]
        if parts:
            meds.append(" — ".join(parts))
    if meds:
        sections.append({"title": "Medications", "text": "; ".join(meds)})

    results = []
    for result in root.findall(".//ccr:Body/ccr:Results/ccr:Result", namespaces=ns):
        description = _text(result.find("ccr:Description/ccr:Text", namespaces=ns))
        value = _text(result.find("ccr:Value/ccr:Text", namespaces=ns))
        units = _text(result.find("ccr:Value/ccr:Units", namespaces=ns))
        date = _text(result.find("ccr:Test/ccr:DateTime/ccr:ExactDateTime", namespaces=ns))
        parts = [part for part in [description, value, units, date] if part]
        if parts:
            results.append(" ".join(parts))
    if results:
        sections.append({"title": "Results", "text": "; ".join(results)})

    summary = {
        "title": _find_text(".//ccr:Title/ccr:Text"),
        "effective_time": _find_text(".//ccr:DateTime/ccr:ExactDateTime"),
        "document_code": {
            "code": _find_text(".//ccr:CCRDocumentObjectID"),
            "code_system": None,
            "display_name": "Continuity of Care Record",
        },
        "confidentiality_code": None,
        "language_code": _find_text(".//ccr:Language/ccr:Text"),
        "patient": {k: v for k, v in patient.items() if v} if patient else None,
        "authors": [author for author in authors if any(author.values())],
        "custodian": {
            "id": None,
            "name": actors.get(from_actor_id, {}).get("organization") if from_actor_id else None
        },
        "sections": sections,
    }
    return summary


def _extract_clinical_document_summary(root: ElementTree.Element) -> Dict[str, Any]:
    tag = root.tag.split("}")[-1]
    if tag == "ClinicalDocument":
        return _extract_cda_summary(root)
    if tag == "ContinuityOfCareRecord":
        return _extract_ccr_summary(root)
    return {}
async def _process_clinical_document_parser(
    activity: Dict[str, Any],
    context: WorkflowContext,
    document_type: str,
) -> ActivityResult:
    config = activity.get("config", {})
    payload = _resolve_payload_from_context(
        context,
        config,
        [f"{document_type}_payload", f"{document_type}_document", "clinical_document"]
    )

    if not payload:
        return _build_result(
            ActivityStatus.FAILED,
            f"{document_type.upper()} parser failed",
            {},
            error="No XML document payload available"
        )

    payload_str = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
    try:
        root = _parse_xml_document(payload_str)
    except ElementTree.ParseError as exc:
        return _build_result(
            ActivityStatus.FAILED,
            f"{document_type.upper()} parser failed",
            {},
            error=f"XML parsing error: {exc}"
        )

    summary = _extract_clinical_document_summary(root)

    # Extract variables from clinical document summary (consistent with HL7 parser)
    variable_definitions = config.get("variables", [])
    extracted_values: Dict[str, Any] = {}

    for var_def in variable_definitions:
        var_name = var_def.get("name")
        var_source = var_def.get("source")  # Path like "patient.full_name", "patient.id"
        var_default = var_def.get("default", "")

        if var_name and var_source:
            # Extract from summary using JSON path
            value = _extract_json_path(summary, var_source)
            if value is None:
                value = var_default
            extracted_values[var_name] = value
            context.variables[var_name] = value

    store_as = config.get("store_parsed_as", f"{document_type}_document")
    parsed_document = {"document": payload_str, "summary": summary}
    _set_context_variable(context, store_as, parsed_document)
    _set_context_variable(context, f"{document_type}_summary", summary)

    return _build_result(
        ActivityStatus.COMPLETED,
        f"{document_type.upper()} document parsed",
        {
            "summary": summary,
            "extracted_variables": extracted_values,
        },
        variables={store_as: parsed_document, f"{document_type}_summary": summary, **extracted_values}
    )


async def _process_clinical_document_transformer(
    activity: Dict[str, Any],
    context: WorkflowContext,
    document_type: str,
) -> ActivityResult:
    config = activity.get("config", {})
    document = _resolve_payload_from_context(context, config, [f"{document_type}_document", "clinical_document"])

    if not document:
        return _build_result(
            ActivityStatus.FAILED,
            f"{document_type.upper()} transformation failed",
            {},
            error="Document payload missing"
        )

    existing_summary = context.variables.get(f"{document_type}_summary", {}) or {}
    if isinstance(document, dict):
        document_str = document.get("document", "")
        existing_summary = document.get("summary") or document.get("metadata") or existing_summary
    else:
        document_str = str(document)

    rules = _safe_json_loads(config.get("transformation_rules")) or []
    transformed_metadata = dict(existing_summary)
    if isinstance(rules, list) and existing_summary:
        transformed_metadata.update(_apply_simple_rules(existing_summary, rules))

    output_variable = config.get("output_variable", f"{document_type}_transformed")
    transformed_payload = {"document": document_str, "metadata": transformed_metadata}
    _set_context_variable(context, output_variable, transformed_payload)

    return _build_result(
        ActivityStatus.COMPLETED,
        f"{document_type.upper()} document transformed",
        {"transformed_metadata": transformed_metadata},
        variables={output_variable: transformed_payload}
    )


async def _process_clinical_document_translator(
    activity: Dict[str, Any],
    context: WorkflowContext,
    document_type: str,
) -> ActivityResult:
    config = activity.get("config", {})
    payload = _resolve_payload_from_context(context, config, [f"{document_type}_transformed", f"{document_type}_document"])
    target_format = (config.get("target_format") or "json").lower()
    store_as = config.get("store_result_as", f"{document_type}_{target_format}")

    summary = context.variables.get(f"{document_type}_summary", {}) or {}

    if isinstance(payload, dict):
        document = payload.get("document", "")
        metadata = payload.get("metadata") or payload.get("summary") or summary
    else:
        document = payload
        metadata = summary

    if not summary and isinstance(metadata, dict):
        summary = metadata

    if target_format == "english":
        translated = _build_clinical_document_translation(summary, document, target_format)
    else:
        translated = {"document": document, "metadata": metadata, "summary": summary, "target_format": target_format}

    translated["target_format"] = target_format
    _set_context_variable(context, store_as, translated)

    return _build_result(
        ActivityStatus.COMPLETED,
        f"{document_type.upper()} document translated",
        translated,
        variables={store_as: translated}
    )


async def _process_clinical_document_sender(
    activity: Dict[str, Any],
    context: WorkflowContext,
    document_type: str,
) -> ActivityResult:
    config = activity.get("config", {})
    endpoint = config.get("endpoint_url")
    if not endpoint:
        return _build_result(
            ActivityStatus.FAILED,
            f"{document_type.upper()} sender failed",
            {},
            error="Destination endpoint required"
        )

    payload_variable = config.get("payload_variable", f"{document_type}_document")
    payload_present = payload_variable in context.variables

    return _build_result(
        ActivityStatus.COMPLETED,
        f"{document_type.upper()} sender simulated",
        {"endpoint": endpoint, "transport": config.get("transport_protocol", "https"), "simulated": True},
        variables={f"{document_type}_sender_status": "SIMULATED", f"{document_type}_payload_present": payload_present}
    )


# Exposed document-specific handlers
async def process_cda_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_parser(activity, context, "cda")


async def process_cda_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_transformer(activity, context, "cda")


async def process_cda_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_translator(activity, context, "cda")


async def process_cda_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_sender(activity, context, "cda")


async def process_ccd_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_parser(activity, context, "ccd")


async def process_ccd_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_transformer(activity, context, "ccd")


async def process_ccd_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_translator(activity, context, "ccd")


async def process_ccd_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_sender(activity, context, "ccd")


async def process_ccr_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_parser(activity, context, "ccr")


async def process_ccr_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_transformer(activity, context, "ccr")


async def process_ccr_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_translator(activity, context, "ccr")


async def process_ccr_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    return await _process_clinical_document_sender(activity, context, "ccr")


# --------------------------------------------------------------------------- #
# Terminology Activities (SNOMED CT, ICD, LOINC, etc.)                        #
# --------------------------------------------------------------------------- #

TERMINOLOGY_DISPLAY_NAMES = {
    "snomed": "SNOMED CT",
    "icd10": "ICD-10",
    "icd9": "ICD-9",
    "loinc": "LOINC",
    "rxnorm": "RxNorm",
}


def _normalize_code_system(code_system: Optional[str]) -> str:
    if not code_system:
        return "generic"
    return code_system.replace("-", "").replace("_", "").lower()


async def process_terminology_lookup_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    code = config.get("code") or context.variables.get("code")
    code_system_key = _normalize_code_system(config.get("code_system"))
    display_name = TERMINOLOGY_DISPLAY_NAMES.get(code_system_key, code_system_key.upper())

    if not code:
        return _build_result(
            ActivityStatus.FAILED,
            "Terminology lookup failed",
            {},
            error="No code provided"
        )

    concept = {
        "code": code,
        "display": config.get("display") or f"Concept for {code}",
        "system": display_name,
        "version": config.get("version") or "latest"
    }

    store_as = config.get("store_result_as", f"{code_system_key}_concept")
    _set_context_variable(context, store_as, concept)

    return _build_result(
        ActivityStatus.COMPLETED,
        "Terminology concept resolved",
        {"concept": concept},
        variables={store_as: concept}
    )


async def process_terminology_mapper_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    source_code = config.get("source_code") or context.variables.get("code")
    mapping_table = _safe_json_loads(config.get("mapping_table")) or {}

    mapped = None
    if isinstance(mapping_table, dict) and source_code in mapping_table:
        mapped = mapping_table[source_code]

    if not mapped and "default" in config:
        mapped = config.get("default")

    store_as = config.get("store_result_as", "mapped_code")
    _set_context_variable(context, store_as, mapped)

    return _build_result(
        ActivityStatus.COMPLETED,
        "Terminology mapping completed",
        {"mapped_code": mapped, "source_code": source_code},
        variables={store_as: mapped}
    )


async def process_terminology_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    source_code = config.get("source_code") or context.variables.get("code")
    target_system = _normalize_code_system(config.get("target_system"))

    translation_profile = _safe_json_loads(config.get("translation_profile")) or {}
    translated = None
    if isinstance(translation_profile, dict):
        translated = translation_profile.get(source_code, config.get("default"))

    store_as = config.get("store_result_as", f"{target_system}_code")
    _set_context_variable(context, store_as, translated)

    return _build_result(
        ActivityStatus.COMPLETED,
        "Terminology translation completed",
        {"target_system": target_system, "translated_code": translated},
        variables={store_as: translated}
    )


async def process_terminology_publisher_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    config = activity.get("config", {})
    endpoint = config.get("endpoint_url")
    if not endpoint:
        return _build_result(
            ActivityStatus.FAILED,
            "Terminology publisher failed",
            {},
            error="Endpoint URL required"
        )

    payload_variable = config.get("payload_variable", "mapped_code")
    payload = context.variables.get(payload_variable)

    return _build_result(
        ActivityStatus.COMPLETED,
        "Terminology publisher simulated",
        {"endpoint": endpoint, "payload_variable": payload_variable, "simulated": True},
        variables={"terminology_publisher_status": "SIMULATED", payload_variable: payload}
    )
