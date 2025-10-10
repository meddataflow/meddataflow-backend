"""
DICOM (Digital Imaging and Communications in Medicine) Processor
Handles parsing, transformation, translation, and sending of DICOM medical images.
"""
import base64
import io
import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple

from models.workflow_models import ActivityResult, ActivityStatus, WorkflowContext

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helper Functions
# --------------------------------------------------------------------------- #

def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.debug("dicom_processor: failed to parse JSON, using raw text")
    return value


def _resolve_payload_from_context(
    context: WorkflowContext,
    config: Dict[str, Any],
    default_keys: Iterable[str],
) -> Optional[Any]:
    """Resolve DICOM payload from workflow context"""
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


def _apply_simple_rules(
    source: Dict[str, Any],
    rules: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply transformation rules"""
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
        value = source.get(source_path) if source_path else None

        if operation == "map":
            mapping = _safe_json_loads(rule.get("mapping")) or rule.get("mapping", {})
            if isinstance(mapping, dict):
                value = mapping.get(value, rule.get("default"))
        elif operation == "copy":
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
# DICOM Parsing
# --------------------------------------------------------------------------- #

def _decode_dicom_payload(payload: Any) -> Tuple[Optional[bytes], Dict[str, Any]]:
    """Decode DICOM payload from various formats"""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload), {}
    if isinstance(payload, str):
        try:
            binary = base64.b64decode(payload, validate=True)
            return binary, _extract_dicom_metadata(binary)
        except Exception as e:
            logger.debug(f"_decode_dicom_payload: base64 decode failed: {e}, treating as JSON")
            return None, _safe_json_loads(payload) or {}
    if isinstance(payload, dict):
        return None, payload
    return None, {}


def _extract_dicom_metadata(binary_payload: bytes) -> Dict[str, Any]:
    """Extract metadata from DICOM binary file"""
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

        # Patient demographics
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

        # Equipment
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

        # Additional attributes
        additional_info = {
            "ContrastBolusAgent": _safe_attr("ContrastBolusAgent"),
            "ProtocolName": _safe_attr("ProtocolName"),
            "KVP": _safe_attr("KVP"),
            "ExposureTime": _safe_attr("ExposureTime"),
            "XRayTubeCurrent": _safe_attr("XRayTubeCurrent"),
            "WindowCenter": _safe_attr("WindowCenter"),
            "WindowWidth": _safe_attr("WindowWidth"),
        }
        metadata.update({k: v for k, v in additional_info.items() if v})

        # Remove None values
        metadata = {k: v for k, v in metadata.items() if v}

    except Exception as exc:
        metadata = {"parse_warning": str(exc)}

    return metadata


async def process_dicom_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Parse DICOM file and extract metadata"""
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

    if metadata:
        summary["metadata"] = metadata

    store_as = config.get("store_parsed_as", "dicom_metadata")
    _set_context_variable(context, store_as, metadata or summary)

    return _build_result(
        ActivityStatus.COMPLETED,
        "DICOM payload parsed",
        summary,
        variables={store_as: metadata or summary, "dicom_summary": summary}
    )


# --------------------------------------------------------------------------- #
# DICOM Transformation
# --------------------------------------------------------------------------- #

async def process_dicom_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Transform DICOM metadata using rules"""
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


# --------------------------------------------------------------------------- #
# DICOM Translation to English
# --------------------------------------------------------------------------- #

async def process_dicom_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Translate DICOM metadata to human-readable English"""
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

    # Extract actual metadata if nested
    if "metadata" in metadata and isinstance(metadata["metadata"], dict):
        actual_metadata = metadata["metadata"]
    else:
        actual_metadata = metadata

    target_format = (config.get("target_format") or "english").lower()
    store_as = config.get("store_result_as", f"dicom_{target_format}")

    if target_format == "english":
        # Build human-readable summary
        patient_name = actual_metadata.get("PatientName", "Unknown Patient")
        modality = actual_metadata.get("Modality", "Unknown")
        study_date = actual_metadata.get("StudyDate", "Unknown")
        body_part = actual_metadata.get("BodyPartExamined", "Unknown")
        institution = actual_metadata.get("InstitutionName", "Unknown Institution")

        # Format dates
        formatted_date = study_date
        if study_date and study_date != "Unknown" and len(study_date) == 8:
            try:
                formatted_date = f"{study_date[0:4]}-{study_date[4:6]}-{study_date[6:8]}"
            except:
                pass

        # Build summary
        modality_descriptions = {
            "CT": "Computed Tomography (CT) scan",
            "MR": "Magnetic Resonance Imaging (MRI) scan",
            "CR": "Computed Radiography (X-Ray)",
            "DX": "Digital Radiography (X-Ray)",
            "US": "Ultrasound examination",
            "XA": "X-Ray Angiography",
        }
        modality_desc = modality_descriptions.get(modality, f"{modality} imaging study" if modality != "Unknown" else "Medical imaging study")

        summary_parts = [modality_desc]
        if patient_name != "Unknown Patient":
            summary_parts.append(f"for patient {patient_name}")
        if body_part != "Unknown":
            summary_parts.append(f"examining {body_part.lower()}")
        if formatted_date != "Unknown":
            summary_parts.append(f"performed on {formatted_date}")
        if institution != "Unknown Institution":
            summary_parts.append(f"at {institution}")

        summary = " ".join(summary_parts)

        # Build detailed list
        details = []
        if actual_metadata.get("PatientID"):
            details.append(f"Patient ID: {actual_metadata['PatientID']}")
        if actual_metadata.get("StudyDescription"):
            details.append(f"Study: {actual_metadata['StudyDescription']}")
        if actual_metadata.get("Manufacturer"):
            details.append(f"Equipment: {actual_metadata['Manufacturer']}")

        translated = {
            "summary": summary,
            "details": details,
            "modality": modality,
            "patient_name": patient_name,
            "study_date": formatted_date,
            "body_part": body_part,
            "institution": institution,
            "raw_metadata": actual_metadata
        }
    else:
        translated = {
            "resourceType": "ImagingStudy",
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


# --------------------------------------------------------------------------- #
# DICOM Sender
# --------------------------------------------------------------------------- #

async def process_dicom_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Send DICOM file to endpoint (simulated)"""
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

    return _build_result(
        ActivityStatus.COMPLETED,
        "DICOM sender simulated",
        {"endpoint": endpoint, "transport": config.get("transport_protocol", "dicom"), "simulated": True},
        variables={"dicom_sender_status": "SIMULATED", "dicom_sender_payload_present": payload is not None}
    )
