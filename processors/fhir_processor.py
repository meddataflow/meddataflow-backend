"""
FHIR (Fast Healthcare Interoperability Resources) Processor
Handles parsing, transformation, translation, and sending of FHIR resources.
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from models.workflow_models import ActivityResult, ActivityStatus, WorkflowContext
from services.fhir_summary import (
    FHIRParsingError,
    build_fhir_translation_summary,
    extract_common_fhir_values,
    parse_fhir_resource,
)

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
            logger.debug("fhir_processor: failed to parse JSON, using raw text")
    return value


def _resolve_payload_from_context(
    context: WorkflowContext,
    config: Dict[str, Any],
    default_keys: Iterable[str],
) -> Optional[Any]:
    """Resolve FHIR payload from workflow context"""
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
    """Apply transformation/translation rules"""
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
# FHIR Parser
# --------------------------------------------------------------------------- #

async def process_fhir_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Parse FHIR resource and extract values"""
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

    extraction_rules = _safe_json_loads(config.get("extraction_rules")) or config.get("extraction_rules")
    extracted_values: Dict[str, Any] = {}
    if isinstance(extraction_rules, list):
        for rule in extraction_rules:
            name = rule.get("name")
            path = rule.get("path")
            if name and path:
                extracted_values[name] = _extract_json_path(resource, path)

    # Supply smart defaults if the caller did not define extraction rules
    auto_values = extract_common_fhir_values(resource)
    for key, value in auto_values.items():
        extracted_values.setdefault(key, value)

    store_as = config.get("store_parsed_as", "fhir_resource")
    _set_context_variable(context, store_as, resource)
    context.variables.update(extracted_values)

    return _build_result(
        ActivityStatus.COMPLETED,
        "FHIR message parsed",
        {
            "resource_type": resource.get("resourceType"),
            "extracted_values": extracted_values,
            "resource": resource,
        },
        variables={store_as: resource, "fhir_resource": resource, **extracted_values}
    )


# --------------------------------------------------------------------------- #
# FHIR Transformer
# --------------------------------------------------------------------------- #

async def process_fhir_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Transform FHIR resource using rules"""
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


# --------------------------------------------------------------------------- #
# FHIR Translator
# --------------------------------------------------------------------------- #

async def process_fhir_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Translate FHIR resource to target format"""
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
        output_payload = {
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


# --------------------------------------------------------------------------- #
# FHIR Sender
# --------------------------------------------------------------------------- #

async def process_fhir_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Send FHIR resource to endpoint"""
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
