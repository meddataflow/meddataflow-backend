"""
NCPDP (National Council for Prescription Drug Programs) Message Processor
Handles parsing, transformation, translation, and sending of NCPDP pharmacy messages.

Supports two main formats:
1. NCPDP Telecommunication D.0 - B1 Claim (segment/field format)
2. NCPDP SCRIPT 2017071 - NewRx (XML format)
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree

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
            logger.debug("ncpdp_processor: failed to parse JSON, using raw text")
    return value


def _resolve_payload_from_context(
    context: WorkflowContext,
    config: Dict[str, Any],
    default_keys: Iterable[str],
) -> Optional[Any]:
    """Resolve NCPDP payload from workflow context"""
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


def _normalize_parsed_wrapper(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten translator inputs that wrap the actual NCPDP payload under a parsed_data key.
    The translator is sometimes invoked with the direct parsed payload and other times
    with a container like {"parsed_data": {...}, "format": "script_xml"}. This helper
    unifies the structure so downstream translation logic always sees the expected fields.
    """
    current = message

    while isinstance(current, dict) and isinstance(current.get("parsed_data"), dict):
        nested = current["parsed_data"]
        # Start with the nested payload, then layer in any top-level metadata
        merged: Dict[str, Any] = dict(nested)
        for key, value in current.items():
            if key == "parsed_data":
                continue
            # Preserve additional metadata (e.g., format) when not already present
            merged.setdefault(key, value)
        current = merged

    return current


_SEGMENT_NAME_MAP = {
    "header": "TRANSACTION_HEADER",
    "transaction header": "TRANSACTION_HEADER",
    "transactionheader": "TRANSACTION_HEADER",
    "patient": "PATIENT",
    "insurance": "INSURANCE",
    "payer": "INSURANCE",
    "prescriber": "PRESCRIBER",
    "pharmacy": "PHARMACY",
    "claim": "CLAIM",
    "pricing": "PRICING",
    "dur": "DUR_PPS",
    "dur pps": "DUR_PPS",
    "dur/pps": "DUR_PPS",
    "cob": "COB_OTHER_PAYMENTS",
    "cob other payments": "COB_OTHER_PAYMENTS",
    "cob/other_payments": "COB_OTHER_PAYMENTS",
    "other payments": "COB_OTHER_PAYMENTS",
    "compound": "COMPOUND",
    "clinical": "CLINICAL",
    "additional documentation": "ADDITIONAL_DOCUMENTATION",
    "other": "OTHER"
}


def _normalize_segment_name(name: str) -> str:
    normalized = (
        name.strip().lower()
        .replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
    )
    normalized = " ".join(normalized.split())
    return _SEGMENT_NAME_MAP.get(normalized, name.strip().upper())


def _detect_ncpdp_format(payload_str: str) -> str:
    """Detect NCPDP format type"""
    payload_trimmed = payload_str.strip()

    # Check for XML format (SCRIPT)
    if payload_trimmed.startswith("<?xml") or payload_trimmed.startswith("<Message") or payload_trimmed.startswith("<NewRx"):
        return "script_xml"

    # Check for telecommunication format indicators
    if any(indicator in payload_str for indicator in ["AM01", "AM02", "AM03", "B1", "B2", "B3"]):
        return "telecommunication"

    # Default to telecommunication if it has delimited pairs
    if "=" in payload_str:
        return "telecommunication"

    return "unknown"


# --------------------------------------------------------------------------- #
# NCPDP Telecommunication D.0 Parsing (B1 Claim)
# --------------------------------------------------------------------------- #

def _parse_telecommunication_segments(payload: str) -> Dict[str, Any]:
    """
    Parse NCPDP Telecommunication D.0 format (segment/field format)
    Example segments: Transaction Header, Patient, Insurance, Claim, Pricing
    """
    result: Dict[str, Any] = {
        "format": "telecommunication_d0",
        "segments": {},
        "raw_fields": {}
    }
    unique_field_codes: set[str] = set()

    def store_field(code: str, value: str, container: Dict[str, Any]) -> None:
        if not code:
            return
        canonical = code.strip()
        if not canonical:
            return
        canonical = canonical.upper()
        value_str = value.strip()
        if not value_str:
            value_str = ""

        container[canonical] = value_str
        result["raw_fields"][canonical] = value_str
        unique_field_codes.add(canonical)

    def enrich_structured_fields() -> None:
        segments = result.get("segments") or {}
        if not isinstance(segments, dict):
            return

        fields = result.get("raw_fields") or {}

        header_segment = _first_segment_entry(segments, "TRANSACTION_HEADER")
        patient_segment = _first_segment_entry(segments, "PATIENT")
        insurance_segment = _first_segment_entry(segments, "INSURANCE")
        claim_segment = _first_segment_entry(segments, "CLAIM")
        pricing_segment = _first_segment_entry(segments, "PRICING")
        prescriber_segment = _first_segment_entry(segments, "PRESCRIBER")
        pharmacy_segment = _first_segment_entry(segments, "PHARMACY")

        transaction_code = (_lookup_field(header_segment, fields, "103-A3", "AM03") or "").upper()
        transaction_types = {
            "B1": "Claim Billing",
            "B2": "Claim Reversal",
            "B3": "Claim Rebill",
            "E1": "Eligibility Verification",
            "P1": "Prior Authorization Request",
            "P2": "Prior Authorization Response"
        }

        header_info = {
            "bin_number": _lookup_field(header_segment, fields, "101-A1", "AM01"),
            "version": _lookup_field(header_segment, fields, "102-A2", "AM02"),
            "transaction_code": transaction_code or None,
            "transaction_type": transaction_types.get(transaction_code) if transaction_code else None,
            "processor_control_number": _lookup_field(header_segment, fields, "104-A4", "AM04"),
            "service_provider_id": _lookup_field(header_segment, fields, "202-B2", "AM07"),
            "service_provider_id_qualifier": _lookup_field(header_segment, fields, "201-B1", "AM06"),
            "pharmacy_id": _lookup_field(header_segment, fields, "110-AK"),
            "pharmacy_id_qualifier": _lookup_field(header_segment, fields, "109-A9"),
        }
        header_info_clean = {k: v for k, v in header_info.items() if v not in (None, "", [], {})}
        if header_info_clean:
            result["header"] = header_info_clean

        patient_first = _lookup_field(patient_segment, fields, "311-CB", "CA02")
        patient_last = _lookup_field(patient_segment, fields, "310-CA", "CA01")
        patient_name = " ".join(part for part in [patient_first, patient_last] if part).strip()
        patient_dob = _lookup_field(patient_segment, fields, "304-C4", "CA03")
        patient_gender_code = _lookup_field(patient_segment, fields, "305-C5", "CA04")
        patient_gender_label = _expand_gender(patient_gender_code)
        patient_address_parts = [
            _lookup_field(patient_segment, fields, "322-CM"),
            _lookup_field(patient_segment, fields, "323-CN"),
            _lookup_field(patient_segment, fields, "324-CO"),
            _lookup_field(patient_segment, fields, "325-CP"),
        ]
        patient_address = ", ".join(part for part in patient_address_parts if part)

        patient_info = {
            "first_name": patient_first,
            "last_name": patient_last,
            "full_name": patient_name if patient_name else None,
            "date_of_birth": _format_ncpdp_date(patient_dob) if patient_dob else None,
            "gender": patient_gender_label or patient_gender_code,
            "gender_code": patient_gender_code if patient_gender_label and patient_gender_label != patient_gender_code else None,
            "phone": _lookup_field(patient_segment, fields, "307-C7", "CA10"),
            "address": patient_address or None,
            "raw_gender_code": patient_gender_code,
        }
        patient_info_clean = {k: v for k, v in patient_info.items() if v not in (None, "", [], {})}
        if patient_info_clean:
            result["patient"] = patient_info_clean

        insurance_info = {
            "cardholder_id": _lookup_field(insurance_segment, fields, "302-C2", "AM05"),
            "group_id": _lookup_field(insurance_segment, fields, "301-C1"),
            "person_code": _lookup_field(insurance_segment, fields, "303-C3"),
            "plan_id": _lookup_field(insurance_segment, fields, "312-CC"),
            "payer_id": _lookup_field(insurance_segment, fields, "113-AN"),
            "other_coverage_code": _lookup_field(insurance_segment, fields, "308-C8"),
        }
        insurance_info_clean = {k: v for k, v in insurance_info.items() if v not in (None, "", [], {})}
        if insurance_info_clean:
            result["insurance"] = insurance_info_clean

        service_date = _lookup_field(claim_segment, fields, "401-D1", "D1")
        date_written = _lookup_field(claim_segment, fields, "414-DE", "DE")
        claim_info = {
            "service_provider_id": header_info_clean.get("service_provider_id"),
            "date_of_service": _format_ncpdp_date(service_date) if service_date else None,
            "prescription_number": _lookup_field(claim_segment, fields, "402-D2", "D2"),
            "fill_number": _lookup_field(claim_segment, fields, "403-D3", "D3"),
            "product_id": _lookup_field(claim_segment, fields, "407-D7", "D7"),
            "product_id_qualifier": _lookup_field(claim_segment, fields, "436-E1", "E1"),
            "quantity_dispensed": _lookup_field(claim_segment, fields, "442-E7", "D8"),
            "metric_quantity": _lookup_field(claim_segment, fields, "408-D8"),
            "days_supply": _lookup_field(claim_segment, fields, "405-D5", "D4"),
            "refills_authorized": _lookup_field(claim_segment, fields, "415-DF", "DF"),
            "daw_code": _lookup_field(claim_segment, fields, "419-DJ", "DJ"),
            "submission_clarification_code": _lookup_field(claim_segment, fields, "420-DK", "DK"),
            "date_written": _format_ncpdp_date(date_written) if date_written else None,
        }
        claim_info_clean = {k: v for k, v in claim_info.items() if v not in (None, "", [], {})}
        if claim_info_clean:
            result["claim"] = claim_info_clean

        pricing_info = {
            "ingredient_cost": _lookup_field(pricing_segment, fields, "409-D9", "D9"),
            "dispensing_fee": _lookup_field(pricing_segment, fields, "412-DC", "DC"),
            "total_amount_paid": _lookup_field(pricing_segment, fields, "426-DQ", "DQ"),
            "patient_pay_amount": _lookup_field(pricing_segment, fields, "433-DX", "DU"),
            "other_amount_paid": _lookup_field(pricing_segment, fields, "438-E3", "431-DV"),
        }
        pricing_info_clean = {k: v for k, v in pricing_info.items() if v not in (None, "", [], {})}
        if pricing_info_clean:
            result["pricing"] = pricing_info_clean

        prescriber_info = {
            "identifier": _lookup_field(prescriber_segment, fields, "411-DB", "E1"),
            "identifier_qualifier": _lookup_field(prescriber_segment, fields, "427-DR"),
            "last_name": _lookup_field(prescriber_segment, fields, "498-PM", "E3"),
            "phone": _lookup_field(prescriber_segment, fields, "421-DK"),
        }
        prescriber_info_clean = {k: v for k, v in prescriber_info.items() if v not in (None, "", [], {})}
        if prescriber_info_clean:
            result["prescriber"] = prescriber_info_clean

        pharmacy_info = {
            "identifier": _lookup_field(pharmacy_segment, fields, "229-DC", "202-B2", "110-AK"),
            "identifier_qualifier": _lookup_field(pharmacy_segment, fields, "465-EY", "201-B1", "109-A9"),
            "phone": _lookup_field(pharmacy_segment, fields, "444-E9"),
            "submitted_provider_id": header_info_clean.get("service_provider_id"),
        }
        pharmacy_info_clean = {k: v for k, v in pharmacy_info.items() if v not in (None, "", [], {})}
        if pharmacy_info_clean:
            result["pharmacy"] = pharmacy_info_clean

        dur_entries = []
        for idx, entry in enumerate(_segment_entries(segments, "DUR_PPS"), start=1):
            dur_entries.append({
                "reason_code": _lookup_field(entry, fields, "439-E4"),
                "professional_service_code": _lookup_field(entry, fields, "440-E5"),
                "result_code": _lookup_field(entry, fields, "441-E6"),
                "level_of_effort": _lookup_field(entry, fields, "473-7E"),
                "message": _lookup_field(entry, fields, "474-8E"),
                "sequence": idx,
                "label": entry.get("_segment_label"),
            })
        dur_entries_clean = [
            {k: v for k, v in entry.items() if v not in (None, "", [], {})}
            for entry in dur_entries if any(v not in (None, "", [], {}) for v in entry.values())
        ]
        if dur_entries_clean:
            result["dur_pps"] = dur_entries_clean

        cob_entries = []
        for idx, entry in enumerate(_segment_entries(segments, "COB_OTHER_PAYMENTS"), start=1):
            cob_entries.append({
                "other_payment_count": _lookup_field(entry, fields, "471-5E"),
                "coverage_type": _lookup_field(entry, fields, "337-4C"),
                "other_amount_paid": _lookup_field(entry, fields, "338-5C"),
                "patient_amount_paid": _lookup_field(entry, fields, "351-NP"),
                "sequence": idx,
                "label": entry.get("_segment_label"),
            })
        cob_entries_clean = [
            {k: v for k, v in entry.items() if v not in (None, "", [], {})}
            for entry in cob_entries if any(v not in (None, "", [], {}) for v in entry.values())
        ]
        if cob_entries_clean:
            result["cob"] = cob_entries_clean

        compound_segment = _first_segment_entry(segments, "COMPOUND")
        compound_info = {
            "compound_type": _lookup_field(compound_segment, fields, "450-EF"),
        }
        compound_info_clean = {k: v for k, v in compound_info.items() if v not in (None, "", [], {})}
        if compound_info_clean:
            result["compound"] = compound_info_clean

        clinical_segment = _first_segment_entry(segments, "CLINICAL")
        clinical_info = {
            "blood_pressure": _lookup_field(clinical_segment, fields, "491-VE"),
            "pulse": _lookup_field(clinical_segment, fields, "492-WE"),
            "weight": _lookup_field(clinical_segment, fields, "493-XE"),
        }
        clinical_info_clean = {k: v for k, v in clinical_info.items() if v not in (None, "", [], {})}
        if clinical_info_clean:
            result["clinical"] = clinical_info_clean

        patient_display_name = patient_info_clean.get("full_name") if patient_info_clean else None
        if not patient_display_name:
            patient_display_name = None

        summary_parts = [f"NCPDP {transaction_types.get(transaction_code, 'Transaction ' + transaction_code if transaction_code else 'Transaction')}"]
        version = header_info_clean.get("version")
        if version:
            summary_parts.append(f"(Version {version})")
        if patient_display_name:
            summary_parts.append(f"for {patient_display_name}")

        summary = " ".join(summary_parts).strip()

        product_id = claim_info_clean.get("product_id")
        quantity = claim_info_clean.get("quantity_dispensed")
        days_supply = claim_info_clean.get("days_supply")
        drug_parts = []
        if product_id:
            qualifier = claim_info_clean.get("product_id_qualifier")
            qualifier_label = "NDC" if qualifier == "03" else "Product"
            drug_parts.append(f"{qualifier_label} {product_id}")
        if quantity:
            drug_parts.append(f"Qty {quantity}")
        if days_supply:
            drug_parts.append(f"{days_supply} day supply")
        if drug_parts:
            summary = f"{summary}: {', '.join(drug_parts)}"

        total_amount_paid = pricing_info_clean.get("total_amount_paid")
        if total_amount_paid:
            summary = f"{summary}. Total submitted ${total_amount_paid}"

        pharmacy_identifier = pharmacy_info_clean.get("identifier") if pharmacy_info_clean else None
        service_provider_id = header_info_clean.get("service_provider_id")
        pharmacy_summary = pharmacy_identifier or service_provider_id
        if pharmacy_summary:
            summary = f"{summary}. Submitted by pharmacy {pharmacy_summary}"

        service_date_fmt = claim_info_clean.get("date_of_service")
        if service_date_fmt:
            summary = f"{summary} on {service_date_fmt}"

        summary = summary.rstrip(".") + "."

        result["summary"] = summary
        if transaction_types.get(transaction_code):
            result["transaction_type"] = transaction_types[transaction_code]
        if transaction_code:
            result["transaction_code"] = transaction_code
        if version:
            result["version"] = version
        if header_info_clean.get("bin_number"):
            result["bin"] = header_info_clean["bin_number"]

    # First, support human-readable line format (one segment per line with pipes)
    normalized_payload = payload.replace("\r\n", "\n").replace("\r", "\n")
    line_segments = [line.strip() for line in normalized_payload.split("\n") if line.strip()]
    line_mode = False
    if line_segments and any("|" in line for line in line_segments):
        # Heuristic: treat as line-based telecommunication if fields use ":" or "="
        segments_have_fields = any((":" in segment or "=" in segment) for segment in line_segments)
        if segments_have_fields:
            line_mode = True

    if line_mode:
        logger.debug(f"NCPDP: Entering line mode, found {len(line_segments)} line segments")
        for segment in line_segments:
            parts = [part.strip() for part in segment.split("|") if part.strip()]
            if not parts:
                continue

            raw_segment_name = parts[0]
            segment_id = _normalize_segment_name(raw_segment_name)
            fields: Dict[str, Any] = {"_segment_label": raw_segment_name}

            logger.debug(f"NCPDP: Processing segment '{raw_segment_name}' -> '{segment_id}' with {len(parts)-1} field parts")

            for field_part in parts[1:]:
                delimiter = ":" if ":" in field_part else "=" if "=" in field_part else None
                if not delimiter:
                    logger.debug(f"NCPDP: Skipping field part without delimiter: '{field_part}'")
                    continue
                key, value = field_part.split(delimiter, 1)
                logger.debug(f"NCPDP: Storing field {key.strip()} = {value.strip()}")
                store_field(key, value, fields)

            if len(fields) <= 1:  # Only segment label present
                logger.debug(f"NCPDP: Skipping segment '{segment_id}' - no fields extracted")
                continue

            existing = result["segments"].get(segment_id)
            if existing:
                if isinstance(existing, list):
                    existing.append(fields)
                else:
                    result["segments"][segment_id] = [existing, fields]
            else:
                result["segments"][segment_id] = fields

        logger.debug(f"NCPDP: Parsed {len(result['segments'])} segments, {len(unique_field_codes)} unique fields, {len(result['raw_fields'])} raw fields")
        enrich_structured_fields()

        result["segment_count"] = sum(
            len(v) if isinstance(v, list) else 1 for v in result["segments"].values()
        )
        result["field_count"] = len(unique_field_codes)
        logger.debug(f"NCPDP: Final field_count={result['field_count']}, segment_count={result['segment_count']}")
        return result

    # Fallback to control-character separated format
    separators = ("\u001C", "\u001E", "\u001F", "\x1C", "\x1E", "\x1F")
    segments = [payload]
    for sep in separators:
        if sep in payload:
            segments = payload.split(sep)
            break

    if len(segments) == 1:
        field_seps = ("\u001D", "\x1D", "|")
        for sep in field_seps:
            if sep in payload:
                segments = payload.split(sep)
                break

    for seg_idx, segment in enumerate(segments):
        if not segment.strip():
            continue

        segment_id = f"SEGMENT_{seg_idx:02d}"
        fields = {}

        if "=" in segment or ":" in segment:
            field_parts = (
                segment.split("\x1D") if "\x1D" in segment else
                segment.split("|") if "|" in segment else
                [segment]
            )

            for field_part in field_parts:
                delimiter = "=" if "=" in field_part else ":" if ":" in field_part else None
                if not delimiter:
                    continue
                key, value = field_part.split(delimiter, 1)
                store_field(key, value, fields)

                # Identify segment type based on common field codes
                key_upper = key.upper()
                if key_upper in {"AM01", "AM02", "AM03", "AM04", "101A1", "102A2", "103A3", "104A4"}:
                    segment_id = "TRANSACTION_HEADER"
                elif key_upper in {"CA01", "CA02", "CA03", "310CA", "311CB", "304C4"}:
                    segment_id = "PATIENT"
                elif key_upper in {"AM07", "AM11", "302C2", "301C1"}:
                    segment_id = "INSURANCE"
                elif key_upper in {"D1", "D2", "D3", "D7", "D8", "401D1", "402D2", "403D3", "407D7", "442E7"}:
                    segment_id = "CLAIM"
                elif key_upper in {"D9", "DC", "DQ", "DU", "409D9", "412DC", "426DQ", "433DX"}:
                    segment_id = "PRICING"
                elif key_upper in {"E1", "E2", "E3", "411DB", "498PM"}:
                    segment_id = "PRESCRIBER"
                elif key_upper in {"EM", "EN", "229DC", "202B2"}:
                    segment_id = "PHARMACY"

        if fields:
            existing = result["segments"].get(segment_id)
            if existing:
                if isinstance(existing, list):
                    existing.append(fields)
                else:
                    result["segments"][segment_id] = [existing, fields]
            else:
                result["segments"][segment_id] = fields

    enrich_structured_fields()

    result["segment_count"] = sum(
        len(v) if isinstance(v, list) else 1 for v in result["segments"].values()
    )
    result["field_count"] = len(unique_field_codes)

    return result


def _segment_entries(segments: Dict[str, Any], name: str) -> List[Dict[str, Any]]:
    segment = segments.get(name, {})
    if isinstance(segment, list):
        return [entry for entry in segment if isinstance(entry, dict)]
    if isinstance(segment, dict):
        return [segment]
    return []


def _first_segment_entry(segments: Dict[str, Any], name: str) -> Dict[str, Any]:
    entries = _segment_entries(segments, name)
    return entries[0] if entries else {}


def _candidate_keys(keys: Iterable[str]) -> List[str]:
    candidates: List[str] = []
    for key in keys:
        if not key:
            continue
        key_str = str(key)
        variants = {
            key_str,
            key_str.upper(),
            key_str.lower(),
            key_str.replace("-", ""),
            key_str.replace("-", "").upper(),
            key_str.replace("-", "").lower(),
            key_str.replace("_", ""),
            key_str.replace("_", "").upper(),
            key_str.replace("_", "").lower(),
        }
        candidates.extend(variant for variant in variants if variant)
    # Preserve order but deduplicate
    seen = set()
    ordered: List[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _lookup_field(
    segment: Dict[str, Any],
    all_fields: Dict[str, Any],
    *keys: str
) -> Optional[str]:
    candidates = _candidate_keys(keys)
    for candidate in candidates:
        if isinstance(segment, dict) and candidate in segment:
            value = segment[candidate]
            if value not in (None, ""):
                return value
    for candidate in candidates:
        value = all_fields.get(candidate)
        if value not in (None, ""):
            return value
    return None


def _format_currency(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        clean = str(value).replace("$", "").strip()
        if not clean:
            return None
        amount = float(clean) if "." in clean else float(clean) / 100.0
        return f"${amount:.2f}"
    except (ValueError, TypeError):
        return str(value)


def _expand_gender(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    gender_map = {"0": "Unknown", "1": "Male", "2": "Female", "M": "Male", "F": "Female"}
    return gender_map.get(str(code).upper(), str(code))


def _translate_telecommunication_to_english(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Translate NCPDP Telecommunication format to readable English"""
    segments = parsed.get("segments") or {}
    fields = parsed.get("raw_fields") or {}

    header = _first_segment_entry(segments, "TRANSACTION_HEADER")
    patient_entries = _segment_entries(segments, "PATIENT")
    patient = patient_entries[0] if patient_entries else {}
    insurance_entries = _segment_entries(segments, "INSURANCE")
    insurance = insurance_entries[0] if insurance_entries else {}
    claim_entries = _segment_entries(segments, "CLAIM")
    claim = claim_entries[0] if claim_entries else {}
    pricing_entries = _segment_entries(segments, "PRICING")
    pricing = pricing_entries[0] if pricing_entries else {}
    prescriber_entries = _segment_entries(segments, "PRESCRIBER")
    prescriber = prescriber_entries[0] if prescriber_entries else {}
    pharmacy_entries = _segment_entries(segments, "PHARMACY")
    pharmacy = pharmacy_entries[0] if pharmacy_entries else {}

    bin_number = _lookup_field(header, fields, "101-A1", "AM01")
    version = _lookup_field(header, fields, "102-A2", "AM02")
    transaction_code = (_lookup_field(header, fields, "103-A3", "AM03") or "").upper()
    processor_control = _lookup_field(header, fields, "104-A4", "AM04")
    service_provider_id = _lookup_field(header, fields, "202-B2", "AM07")
    service_provider_qualifier = _lookup_field(header, fields, "201-B1", "AM06")
    pharmacy_id_header = _lookup_field(header, fields, "110-AK")
    pharmacy_id_qual_header = _lookup_field(header, fields, "109-A9")

    transaction_types = {
        "B1": "Claim Billing",
        "B2": "Claim Reversal",
        "B3": "Claim Rebill",
        "E1": "Eligibility Verification",
        "P1": "Prior Authorization Request",
        "P2": "Prior Authorization Response"
    }
    trans_type = transaction_types.get(transaction_code, f"Transaction {transaction_code or 'Unknown'}")

    patient_first = _lookup_field(patient, fields, "311-CB", "CA02")
    patient_last = _lookup_field(patient, fields, "310-CA", "CA01")
    patient_name = " ".join(part for part in [patient_first, patient_last] if part).strip()
    patient_dob = _lookup_field(patient, fields, "304-C4", "CA03")
    patient_gender = _lookup_field(patient, fields, "305-C5", "CA04")
    patient_phone = _lookup_field(patient, fields, "307-C7", "CA10")
    patient_addr1 = _lookup_field(patient, fields, "322-CM")
    patient_city = _lookup_field(patient, fields, "323-CN")
    patient_state = _lookup_field(patient, fields, "324-CO")
    patient_zip = _lookup_field(patient, fields, "325-CP")

    cardholder_id = _lookup_field(insurance, fields, "302-C2", "AM05")
    group_id = _lookup_field(insurance, fields, "301-C1")
    person_code = _lookup_field(insurance, fields, "303-C3")
    payer_id = _lookup_field(insurance, fields, "113-AN")
    other_coverage = _lookup_field(insurance, fields, "308-C8")

    rx_number = _lookup_field(claim, fields, "402-D2", "D2")
    fill_number = _lookup_field(claim, fields, "403-D3", "D3")
    service_date = _lookup_field(claim, fields, "401-D1", "D1")
    product_qualifier = _lookup_field(claim, fields, "436-E1", "E1")
    product_id = _lookup_field(claim, fields, "407-D7", "D7")
    quantity_dispensed = _lookup_field(claim, fields, "442-E7", "D8")
    metric_quantity = _lookup_field(claim, fields, "408-D8")
    days_supply = _lookup_field(claim, fields, "405-D5", "D4")
    daw_code = _lookup_field(claim, fields, "419-DJ", "DJ")
    submission_code = _lookup_field(claim, fields, "420-DK", "DK")
    refills_authorized = _lookup_field(claim, fields, "415-DF", "DF")
    date_written = _lookup_field(claim, fields, "414-DE", "DE")

    ingredient_cost = _format_currency(_lookup_field(pricing, fields, "409-D9", "D9"))
    dispensing_fee = _format_currency(_lookup_field(pricing, fields, "412-DC", "DC"))
    total_amount = _format_currency(_lookup_field(pricing, fields, "426-DQ", "DQ"))
    patient_pay = _format_currency(_lookup_field(pricing, fields, "433-DX", "DU"))
    other_amount = _format_currency(_lookup_field(pricing, fields, "438-E3", "431-DV"))

    prescriber_id = _lookup_field(prescriber, fields, "411-DB", "E1")
    prescriber_id_qualifier = _lookup_field(prescriber, fields, "427-DR")
    prescriber_last = _lookup_field(prescriber, fields, "498-PM", "E3")
    prescriber_phone = _lookup_field(prescriber, fields, "421-DK")

    pharmacy_identifier = _lookup_field(pharmacy, fields, "229-DC", "202-B2", "110-AK")
    pharmacy_phone = _lookup_field(pharmacy, fields, "444-E9")
    pharmacy_id_qual_segment = _lookup_field(pharmacy, fields, "465-EY", "201-B1", "109-A9")

    service_date_fmt = _format_ncpdp_date(service_date) if service_date else None
    date_written_fmt = _format_ncpdp_date(date_written) if date_written else None
    patient_dob_fmt = _format_ncpdp_date(patient_dob) if patient_dob else None
    patient_gender_display = _expand_gender(patient_gender)

    summary_parts = [f"NCPDP {trans_type}"]
    if version:
        summary_parts.append(f"(Version {version})")
    if patient_name:
        summary_parts.append(f"for {patient_name}")
    summary = " ".join(summary_parts).strip()

    drug_parts: List[str] = []
    if product_id:
        qualifier_label = "NDC" if product_qualifier == "03" else "Product"
        drug_parts.append(f"{qualifier_label} {product_id}")
    if quantity_dispensed:
        drug_parts.append(f"Qty {quantity_dispensed}")
    if days_supply:
        drug_parts.append(f"{days_supply} day supply")
    if drug_parts:
        summary = f"{summary}: {', '.join(drug_parts)}"

    if total_amount:
        summary = f"{summary}. Total submitted {total_amount}"

    pharmacy_summary = pharmacy_identifier or service_provider_id
    if pharmacy_summary:
        summary = f"{summary}. Submitted by pharmacy {pharmacy_summary}"

    if service_date_fmt:
        summary = f"{summary} on {service_date_fmt}"

    summary = summary.rstrip(".") + "."

    patient_information: List[str] = []
    if patient_name:
        patient_information.append(f"Patient: {patient_name}")
    if patient_dob_fmt:
        patient_information.append(f"DOB: {patient_dob_fmt}")
    if patient_gender:
        if patient_gender_display != patient_gender:
            patient_information.append(f"Gender: {patient_gender_display} ({patient_gender})")
        else:
            patient_information.append(f"Gender: {patient_gender}")
    address_parts = [part for part in [patient_addr1, patient_city, patient_state, patient_zip] if part]
    if address_parts:
        patient_information.append(f"Address: {', '.join(address_parts)}")
    if patient_phone:
        patient_information.append(f"Phone: {patient_phone}")

    insurance_information: List[str] = []
    if cardholder_id:
        insurance_information.append(f"Cardholder ID: {cardholder_id}")
    if group_id:
        insurance_information.append(f"Group ID: {group_id}")
    if person_code:
        insurance_information.append(f"Person Code: {person_code}")
    if payer_id:
        insurance_information.append(f"Payer ID: {payer_id}")
    if other_coverage:
        insurance_information.append(f"Other Coverage Code: {other_coverage}")

    claim_details: List[str] = []
    if rx_number:
        claim_details.append(f"Rx/Service Reference Number: {rx_number}")
    if fill_number:
        claim_details.append(f"Fill Number: {fill_number}")
    if service_date_fmt:
        claim_details.append(f"Service Date: {service_date_fmt}")
    if date_written_fmt:
        claim_details.append(f"Prescription Written: {date_written_fmt}")
    if product_qualifier:
        claim_details.append(f"Product/Service ID Qualifier: {product_qualifier}")
    if product_id:
        claim_details.append(f"Product/Service ID: {product_id}")
    if quantity_dispensed:
        claim_details.append(f"Quantity Dispensed: {quantity_dispensed}")
    if metric_quantity:
        claim_details.append(f"Metric Quantity: {metric_quantity}")
    if days_supply:
        claim_details.append(f"Days Supply: {days_supply}")
    if refills_authorized:
        claim_details.append(f"Refills Authorized: {refills_authorized}")
    if daw_code:
        claim_details.append(f"DAW Code: {daw_code}")
    if submission_code:
        claim_details.append(f"Submission Clarification Code: {submission_code}")

    pricing_information: List[str] = []
    if ingredient_cost:
        pricing_information.append(f"Ingredient Cost Submitted: {ingredient_cost}")
    if dispensing_fee:
        pricing_information.append(f"Dispensing Fee Submitted: {dispensing_fee}")
    if total_amount:
        pricing_information.append(f"Total Amount Submitted: {total_amount}")
    if patient_pay:
        pricing_information.append(f"Patient Pay Amount: {patient_pay}")
    if other_amount and other_amount != total_amount:
        pricing_information.append(f"Other Amount Claimed Submitted: {other_amount}")

    prescriber_information: List[str] = []
    if prescriber_last:
        prescriber_information.append(f"Prescriber: Dr. {prescriber_last}")
    if prescriber_id:
        prescriber_information.append(f"Prescriber ID: {prescriber_id}")
    if prescriber_id_qualifier:
        prescriber_information.append(f"Prescriber ID Qualifier: {prescriber_id_qualifier}")
    if prescriber_phone:
        prescriber_information.append(f"Prescriber Phone: {prescriber_phone}")

    pharmacy_information: List[str] = []
    if pharmacy_identifier:
        label = "Service Provider ID" if pharmacy_identifier == service_provider_id else "Pharmacy ID"
        pharmacy_information.append(f"{label}: {pharmacy_identifier}")
    if service_provider_id and service_provider_id != pharmacy_identifier:
        pharmacy_information.append(f"Service Provider ID: {service_provider_id}")
    if service_provider_qualifier:
        pharmacy_information.append(f"Service Provider ID Qualifier: {service_provider_qualifier}")
    if pharmacy_id_header:
        pharmacy_information.append(f"Pharmacy ID (Header): {pharmacy_id_header}")
    if pharmacy_id_qual_header:
        pharmacy_information.append(f"Pharmacy ID Qualifier (Header): {pharmacy_id_qual_header}")
    if pharmacy_id_qual_segment and pharmacy_id_qual_segment != pharmacy_id_qual_header:
        pharmacy_information.append(f"Pharmacy ID Qualifier (Segment): {pharmacy_id_qual_segment}")
    if bin_number:
        pharmacy_information.append(f"BIN: {bin_number}")
    if processor_control:
        pharmacy_information.append(f"Processor Control Number: {processor_control}")
    if pharmacy_phone:
        pharmacy_information.append(f"Pharmacy Phone: {pharmacy_phone}")

    dur_information: List[str] = []
    for idx, entry in enumerate(_segment_entries(segments, "DUR_PPS"), start=1):
        reason = _lookup_field(entry, fields, "439-E4")
        service = _lookup_field(entry, fields, "440-E5")
        result_code = _lookup_field(entry, fields, "441-E6")
        effort = _lookup_field(entry, fields, "473-7E")
        message = _lookup_field(entry, fields, "474-8E")
        components: List[str] = []
        if reason:
            components.append(f"Reason: {reason}")
        if service:
            components.append(f"Service: {service}")
        if result_code:
            components.append(f"Result: {result_code}")
        if effort:
            components.append(f"Level of Effort: {effort}")
        if message:
            components.append(f"Message: {message}")
        if components:
            label = entry.get("_segment_label") or f"DUR/PPS {idx}"
            dur_information.append(f"{label}: {'; '.join(components)}")

    cob_information: List[str] = []
    for idx, entry in enumerate(_segment_entries(segments, "COB_OTHER_PAYMENTS"), start=1):
        count = _lookup_field(entry, fields, "471-5E")
        coverage_type = _lookup_field(entry, fields, "337-4C")
        other_paid = _lookup_field(entry, fields, "338-5C")
        patient_paid = _lookup_field(entry, fields, "351-NP")
        components: List[str] = []
        if count:
            components.append(f"Other Payment Count: {count}")
        if coverage_type:
            components.append(f"Other Payer Coverage Type: {coverage_type}")
        if other_paid:
            components.append(f"Other Amount Paid: {_format_currency(other_paid) or other_paid}")
        if patient_paid:
            components.append(f"Patient Amount Paid: {_format_currency(patient_paid) or patient_paid}")
        if components:
            label = entry.get("_segment_label") or f"COB/Other Payments {idx}"
            cob_information.append(f"{label}: {'; '.join(components)}")

    compound_information: List[str] = []
    for idx, entry in enumerate(_segment_entries(segments, "COMPOUND"), start=1):
        compound_type = _lookup_field(entry, fields, "450-EF")
        if compound_type:
            label = entry.get("_segment_label") or f"Compound {idx}"
            compound_information.append(f"{label}: Type {compound_type}")

    clinical_information: List[str] = []
    for idx, entry in enumerate(_segment_entries(segments, "CLINICAL"), start=1):
        blood_pressure = _lookup_field(entry, fields, "491-VE")
        pulse = _lookup_field(entry, fields, "492-WE")
        weight = _lookup_field(entry, fields, "493-XE")
        components: List[str] = []
        if blood_pressure:
            components.append(f"Blood Pressure: {blood_pressure}")
        if pulse:
            components.append(f"Pulse: {pulse}")
        if weight:
            components.append(f"Weight: {weight}")
        if components:
            label = entry.get("_segment_label") or f"Clinical {idx}"
            clinical_information.append(f"{label}: {'; '.join(components)}")

    result = {
        "format": "telecommunication_d0",
        "summary": summary,
        "transaction_type": trans_type,
        "transaction_code": transaction_code or None,
        "version": version,
        "bin": bin_number,
        "processor_control_number": processor_control,
        "service_provider_id": service_provider_id,
        "service_provider_qualifier": service_provider_qualifier,
        "patient_information": patient_information,
        "insurance_information": insurance_information,
        "claim_details": claim_details,
        "pricing_information": pricing_information,
        "prescriber_information": prescriber_information,
        "pharmacy_information": pharmacy_information,
        "dur_pps_information": dur_information,
        "cob_information": cob_information,
        "compound_information": compound_information,
        "clinical_information": clinical_information,
        "raw_segments": segments
    }

    return result


def _format_ncpdp_date(date_str: str) -> str:
    """Format NCPDP date (CCYYMMDD) to readable format"""
    if not date_str or len(date_str) < 8:
        return date_str
    try:
        return f"{date_str[4:6]}/{date_str[6:8]}/{date_str[0:4]}"
    except:
        return date_str


def _extract_nested_value(data: Dict[str, Any], path: str) -> Any:
    """
    Extract value from nested dictionary using dot notation path
    Example: "claim.prescription_number" -> data["claim"]["prescription_number"]
    """
    if not data or not path:
        return None

    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None

    return current


def _extract_ncpdp_variables(parsed: Dict[str, Any], format_type: str) -> Dict[str, Any]:
    """
    Extract commonly used variables from parsed NCPDP message for easy access in workflows
    """
    variables: Dict[str, Any] = {}

    if format_type == "telecommunication" or parsed.get("format") == "telecommunication_d0":
        # Extract from telecommunication format
        claim = parsed.get("claim", {})
        patient = parsed.get("patient", {})
        prescriber = parsed.get("prescriber", {})
        insurance = parsed.get("insurance", {})
        pricing = parsed.get("pricing", {})
        header = parsed.get("header", {})

        # Common claim fields
        variables["rx_number"] = claim.get("prescription_number") or ""
        variables["quantity"] = claim.get("quantity_dispensed") or ""
        variables["product_id"] = claim.get("product_id") or ""
        variables["ndc"] = claim.get("product_id") if claim.get("product_id_qualifier") == "03" else ""
        variables["days_supply"] = claim.get("days_supply") or ""
        variables["date_of_service"] = claim.get("date_of_service") or ""
        variables["refills_authorized"] = claim.get("refills_authorized") or ""
        variables["fill_number"] = claim.get("fill_number") or ""

        # Patient fields
        variables["patient_first_name"] = patient.get("first_name") or ""
        variables["patient_last_name"] = patient.get("last_name") or ""
        variables["patient_name"] = patient.get("full_name") or ""
        variables["patient_dob"] = patient.get("date_of_birth") or ""
        variables["patient_gender"] = patient.get("gender") or ""
        variables["patient_id"] = insurance.get("cardholder_id") or ""
        variables["patient_phone"] = patient.get("phone") or ""
        variables["patient_address"] = patient.get("address") or ""

        # Prescriber fields
        variables["prescriber_id"] = prescriber.get("identifier") or ""
        variables["prescriber_name"] = prescriber.get("last_name") or ""

        # Insurance fields
        variables["cardholder_id"] = insurance.get("cardholder_id") or ""
        variables["group_id"] = insurance.get("group_id") or ""
        variables["payer_id"] = insurance.get("payer_id") or ""

        # Pricing fields
        variables["ingredient_cost"] = pricing.get("ingredient_cost") or ""
        variables["dispensing_fee"] = pricing.get("dispensing_fee") or ""
        variables["total_amount_paid"] = pricing.get("total_amount_paid") or ""
        variables["patient_pay_amount"] = pricing.get("patient_pay_amount") or ""

        # Header fields
        variables["bin_number"] = header.get("bin_number") or ""
        variables["transaction_code"] = header.get("transaction_code") or ""
        variables["transaction_type"] = parsed.get("transaction_type") or ""

        # Legacy compatibility - "drug_name" doesn't exist in NCPDP, use product_id
        variables["drug_name"] = variables["product_id"]

    elif format_type == "script_xml" or parsed.get("format") == "script_xml":
        # Extract from SCRIPT XML format
        patient = parsed.get("patient", {})
        medication = parsed.get("medication", {})
        prescriber = parsed.get("prescriber", {})
        pharmacy = parsed.get("pharmacy", {})

        # Medication fields
        variables["drug_name"] = medication.get("drug_description") or ""
        variables["product_id"] = medication.get("product_code") or ""
        variables["ndc"] = medication.get("product_code") or ""
        variables["quantity"] = medication.get("quantity") or ""
        variables["days_supply"] = medication.get("days_supply") or ""
        variables["refills"] = medication.get("refills") or ""
        variables["rx_number"] = medication.get("prescription_number") or ""
        variables["written_date"] = medication.get("written_date") or ""
        variables["directions"] = parsed.get("directions") or ""

        # Patient fields
        variables["patient_name"] = patient.get("name") or ""
        variables["patient_first_name"] = patient.get("first_name") or ""
        variables["patient_last_name"] = patient.get("last_name") or ""
        variables["patient_dob"] = patient.get("date_of_birth") or ""
        variables["patient_gender"] = patient.get("gender") or ""
        variables["patient_id"] = patient.get("name") or ""  # SCRIPT doesn't have patient ID typically
        variables["patient_phone"] = patient.get("phone") or ""
        variables["patient_address"] = patient.get("address") or ""

        # Prescriber fields
        variables["prescriber_name"] = prescriber.get("name") or ""
        variables["prescriber_npi"] = prescriber.get("npi") or ""
        variables["prescriber_dea"] = prescriber.get("dea") or ""

        # Pharmacy fields
        variables["pharmacy_name"] = pharmacy.get("business_name") or pharmacy.get("name") or ""
        variables["pharmacy_ncpdp_id"] = pharmacy.get("ncpdp_id") or ""
        variables["pharmacy_npi"] = pharmacy.get("npi") or ""

    return variables


# --------------------------------------------------------------------------- #
# NCPDP SCRIPT XML Parsing (NewRx, RxFill, etc.)
# --------------------------------------------------------------------------- #

def _parse_script_xml(payload_str: str) -> Dict[str, Any]:
    """Parse NCPDP SCRIPT XML format"""
    try:
        root = ElementTree.fromstring(payload_str)
    except ElementTree.ParseError as exc:
        logger.error(f"XML parsing error: {exc}")
        return {"error": f"XML parsing failed: {exc}"}

    # Detect message type from root or child elements
    message_type = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    result = {
        "format": "script_xml",
        "message_type": message_type,
        "header": {},
        "body": {},
        "raw_xml": payload_str
    }

    # Define namespace (NCPDP SCRIPT typically uses this)
    ns = {"ncpdp": "http://www.ncpdp.org/schema/SCRIPT"}

    # Parse based on message type
    if message_type in ["Message", "NewRx", "NewRxRequest"]:
        result = _parse_newrx_message(root, ns)
    elif message_type in ["RxFill"]:
        result = _parse_rxfill_message(root, ns)
    elif message_type in ["RefillRequest", "RxRenewalRequest"]:
        result = _parse_refill_request(root, ns)
    else:
        # Generic XML parsing
        result["body"] = _xml_to_dict(root)

    result["format"] = "script_xml"
    result["message_type"] = message_type

    return result


def _parse_newrx_message(root: ElementTree.Element, ns: Dict[str, str]) -> Dict[str, Any]:
    """Parse NewRx SCRIPT message"""
    result = {
        "header": {},
        "patient": {},
        "prescriber": {},
        "pharmacy": {},
        "medication": {},
        "directions": None
    }

    # Helper to find text - searches both with and without namespace
    def find_text(parent, path, default=""):
        """Find element text by path, trying with and without namespace"""
        if not parent:
            return default

        # Split path into parts
        parts = path.split("/")

        # Try with namespace prefix
        elem = parent
        for part in parts:
            if elem is None:
                break
            elem = elem.find(f"ncpdp:{part}", ns)

        if elem is not None and elem.text:
            return elem.text.strip()

        # Try without namespace prefix
        elem = parent
        for part in parts:
            if elem is None:
                break
            elem = elem.find(part)

        if elem is not None and elem.text:
            return elem.text.strip()

        return default

    # Header information (MessageHeader)
    header = root.find("ncpdp:MessageHeader", ns) or root.find("MessageHeader")
    if header is not None:
        result["header"] = {
            "message_id": find_text(header, "MessageID"),
            "sent_time": find_text(header, "SentTime"),
            "from_qualifier": find_text(header, "From"),
            "to_qualifier": find_text(header, "To")
        }

    # Patient information
    patient = root.find("ncpdp:Patient", ns) or root.find("Patient")
    if patient is not None:
        # Build address string
        addr_parts = []
        addr_line1 = find_text(patient, "Address/AddressLine1")
        city = find_text(patient, "Address/City")
        state = find_text(patient, "Address/State")
        zip_code = find_text(patient, "Address/ZIP")

        if addr_line1:
            addr_parts.append(addr_line1)
        if city:
            addr_parts.append(city)
        if state:
            addr_parts.append(state)
        if zip_code:
            addr_parts.append(zip_code)

        address_str = ", ".join(addr_parts) if addr_parts else ""

        # Get patient name
        first_name = find_text(patient, "Name/FirstName")
        last_name = find_text(patient, "Name/LastName")
        name = f"{first_name} {last_name}".strip() if first_name or last_name else ""

        result["patient"] = {
            "name": name,
            "first_name": first_name,
            "last_name": last_name,
            "date_of_birth": find_text(patient, "DateOfBirth"),
            "gender": find_text(patient, "Gender"),
            "address": address_str,
            "phone": find_text(patient, "Contact/Phone/Number"),
            "email": find_text(patient, "Contact/Email")
        }

    # Prescriber information
    prescriber = root.find("ncpdp:Prescriber", ns) or root.find("Prescriber")
    if prescriber is not None:
        # Build prescriber address
        addr_parts = []
        addr_line1 = find_text(prescriber, "Address/AddressLine1")
        city = find_text(prescriber, "Address/City")
        state = find_text(prescriber, "Address/State")
        zip_code = find_text(prescriber, "Address/ZIP")

        if addr_line1:
            addr_parts.append(addr_line1)
        if city:
            addr_parts.append(city)
        if state:
            addr_parts.append(state)
        if zip_code:
            addr_parts.append(zip_code)

        address_str = ", ".join(addr_parts) if addr_parts else ""

        # Get prescriber name
        first_name = find_text(prescriber, "Name/FirstName")
        last_name = find_text(prescriber, "Name/LastName")
        name = f"Dr. {first_name} {last_name}".strip() if first_name or last_name else ""

        result["prescriber"] = {
            "name": name,
            "npi": find_text(prescriber, "Identification/NPI"),
            "dea": find_text(prescriber, "Identification/DEA"),
            "phone": find_text(prescriber, "CommunicationNumbers/PrimaryTelephone"),
            "fax": find_text(prescriber, "CommunicationNumbers/Fax"),
            "address": address_str
        }

    # Pharmacy information
    pharmacy = root.find("ncpdp:Pharmacy", ns) or root.find("Pharmacy")
    if pharmacy is not None:
        # Build pharmacy address
        addr_parts = []
        addr_line1 = find_text(pharmacy, "Address/AddressLine1")
        city = find_text(pharmacy, "Address/City")
        state = find_text(pharmacy, "Address/State")
        zip_code = find_text(pharmacy, "Address/ZIP")

        if addr_line1:
            addr_parts.append(addr_line1)
        if city:
            addr_parts.append(city)
        if state:
            addr_parts.append(state)
        if zip_code:
            addr_parts.append(zip_code)

        address_str = ", ".join(addr_parts) if addr_parts else ""

        result["pharmacy"] = {
            "ncpdp_id": find_text(pharmacy, "Identification/NCPDPID"),
            "npi": find_text(pharmacy, "Identification/NPI"),
            "business_name": find_text(pharmacy, "StoreName"),
            "phone": find_text(pharmacy, "CommunicationNumbers/PrimaryTelephone"),
            "fax": find_text(pharmacy, "CommunicationNumbers/Fax"),
            "address": address_str
        }

    # Medication information
    medication = root.find("ncpdp:MedicationPrescribed", ns) or root.find("MedicationPrescribed")
    if medication is not None:
        # Get quantity
        quantity_value = find_text(medication, "Quantity/Value")
        quantity_unit = find_text(medication, "Quantity/CodeListQualifier")
        quantity_str = f"{quantity_value} {quantity_unit}".strip() if quantity_value else quantity_value

        # Get refills
        refills_value = find_text(medication, "Refills/Value")

        # Get strength and form
        strength = find_text(medication, "DrugCoded/Strength")
        dosage_form = find_text(medication, "DrugCoded/DosageForm")

        # Get directions
        sig_text = find_text(medication, "Sig/SigText")

        result["medication"] = {
            "drug_description": find_text(medication, "DrugDescription"),
            "product_code": find_text(medication, "DrugCoded/ProductCode"),
            "generic_name": find_text(medication, "DrugCoded/GenericName"),
            "strength": strength,
            "dosage_form": dosage_form,
            "quantity": quantity_str,
            "quantity_unit_of_measure": quantity_unit,
            "days_supply": find_text(medication, "DaysSupply"),
            "refills": refills_value,
            "written_date": find_text(medication, "WrittenDate"),
            "prescription_number": find_text(medication, "PrescriptionNumber")
        }

        result["directions"] = sig_text

    return result


def _parse_rxfill_message(root: ElementTree.Element, ns: Dict[str, str]) -> Dict[str, Any]:
    """Parse RxFill SCRIPT message"""
    # Similar structure to NewRx, with additional fill information
    result = _parse_newrx_message(root, ns)

    def find_text(parent, tag, default=""):
        elem = parent.find(f".//ncpdp:{tag}", ns) or parent.find(f".//{tag}")
        return elem.text if elem is not None and elem.text else default

    # Add fill-specific information
    fill_info = root.find(".//ncpdp:FillInformation", ns) or root.find(".//FillInformation")
    if fill_info is not None:
        result["fill_information"] = {
            "fill_number": find_text(fill_info, "FillNumber"),
            "fill_date": find_text(fill_info, "FillDate"),
            "pharmacist": find_text(fill_info, "PharmacistName")
        }

    return result


def _parse_refill_request(root: ElementTree.Element, ns: Dict[str, str]) -> Dict[str, Any]:
    """Parse RefillRequest SCRIPT message"""
    result = _parse_newrx_message(root, ns)

    def find_text(parent, tag, default=""):
        elem = parent.find(f".//ncpdp:{tag}", ns) or parent.find(f".//{tag}")
        return elem.text if elem is not None and elem.text else default

    # Add refill request specific info
    result["request_type"] = "refill_request"
    result["rx_reference_number"] = find_text(root, "RxReferenceNumber")

    return result


def _xml_to_dict(element: ElementTree.Element) -> Dict[str, Any]:
    """Convert XML element to dictionary"""
    result: Dict[str, Any] = {}

    # Add element text if it exists
    if element.text and element.text.strip():
        result["_text"] = element.text.strip()

    # Add attributes
    if element.attrib:
        result["_attributes"] = element.attrib

    # Add child elements
    for child in element:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        child_dict = _xml_to_dict(child)

        if tag in result:
            # Convert to list if multiple elements with same tag
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(child_dict)
        else:
            result[tag] = child_dict

    return result


def _translate_script_xml_to_english(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Translate NCPDP SCRIPT XML to readable English"""
    message_type = parsed.get("message_type", "Unknown")

    patient = parsed.get("patient", {})
    medication = parsed.get("medication", {})
    prescriber = parsed.get("prescriber", {})
    pharmacy = parsed.get("pharmacy", {})

    # Patient summary
    patient_info = []
    if patient.get("first_name") or patient.get("last_name"):
        name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
        patient_info.append(f"Patient: {name}")
    if patient.get("date_of_birth"):
        patient_info.append(f"DOB: {patient.get('date_of_birth')}")
    if patient.get("gender"):
        patient_info.append(f"Gender: {patient.get('gender')}")

    address = patient.get("address")
    if isinstance(address, dict):
        if address.get("street"):
            addr_parts = [address.get("street", "")]
            if address.get("city"):
                addr_parts.append(address.get("city", ""))
            if address.get("state"):
                addr_parts.append(address.get("state", ""))
            if address.get("zip"):
                addr_parts.append(address.get("zip", ""))
            patient_info.append(f"Address: {', '.join(addr_parts)}")
    elif isinstance(address, str) and address.strip():
        patient_info.append(f"Address: {address.strip()}")

    # Medication summary
    medication_info = []
    if medication.get("drug_description"):
        medication_info.append(f"Drug: {medication.get('drug_description')}")
    if medication.get("ndc"):
        medication_info.append(f"NDC: {medication.get('ndc')}")
    if medication.get("quantity"):
        medication_info.append(f"Quantity: {medication.get('quantity')}")
    if medication.get("days_supply"):
        medication_info.append(f"Days Supply: {medication.get('days_supply')}")
    if medication.get("refills"):
        medication_info.append(f"Refills: {medication.get('refills')}")
    if medication.get("directions"):
        medication_info.append(f"Directions: {medication.get('directions')}")

    # Prescriber summary
    prescriber_info = []
    if prescriber.get("first_name") or prescriber.get("last_name"):
        name = f"Dr. {prescriber.get('first_name', '')} {prescriber.get('last_name', '')}".strip()
        prescriber_info.append(f"Prescriber: {name}")
    if prescriber.get("npi"):
        prescriber_info.append(f"NPI: {prescriber.get('npi')}")
    if prescriber.get("dea"):
        prescriber_info.append(f"DEA: {prescriber.get('dea')}")

    # Pharmacy summary
    pharmacy_info = []
    if pharmacy.get("name"):
        pharmacy_info.append(f"Pharmacy: {pharmacy.get('name')}")
    if pharmacy.get("ncpdp_id"):
        pharmacy_info.append(f"NCPDP ID: {pharmacy.get('ncpdp_id')}")
    if pharmacy.get("npi"):
        pharmacy_info.append(f"NPI: {pharmacy.get('npi')}")

    # Enhanced summary with contextual details
    message_labels = {
        "NewRx": "New prescription",
        "NewRxRequest": "New prescription request",
        "RxFill": "Fill notification",
        "RefillRequest": "Refill request",
        "RxRenewalRequest": "Refill request"
    }
    summary_subject = message_labels.get(message_type, message_type or "Message")

    patient_name = patient.get("name") or f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
    prescriber_name = ""
    if prescriber.get("first_name") or prescriber.get("last_name"):
        prescriber_name = f"Dr. {prescriber.get('first_name', '')} {prescriber.get('last_name', '')}".strip()
    elif prescriber.get("name"):
        prescriber_name = prescriber.get("name")

    pharmacy_name = pharmacy.get("name") or pharmacy.get("business_name") or pharmacy.get("ncpdp_id") or ""

    med_text = medication.get("drug_description") or ""
    med_details = []
    if medication.get("quantity"):
        med_details.append(medication.get("quantity"))
    if medication.get("days_supply"):
        med_details.append(f"{medication.get('days_supply')} day supply")
    if med_details and med_text:
        med_text = f"{med_text} ({', '.join(med_details)})"

    summary_parts = [f"NCPDP SCRIPT {summary_subject}"]
    if patient_name:
        summary_parts.append(f"for {patient_name}")
    summary = " ".join(summary_parts).strip()

    if med_text:
        if " for " in summary.lower():
            summary = f"{summary}: {med_text}"
        else:
            summary = f"{summary} for {med_text}"
        summary = summary.replace("::", ":").strip()

    sent_clauses = []
    if prescriber_name:
        sent_clauses.append(f"by {prescriber_name}")
    if pharmacy_name:
        sent_clauses.append(f"to {pharmacy_name}")
    if sent_clauses:
        summary = f"{summary}. Sent {' '.join(sent_clauses)}"

    if medication.get("written_date"):
        summary = f"{summary} on {medication.get('written_date')}"
    summary = summary.rstrip(".") + "."
    summary = " ".join(summary.split())

    return {
        "format": "script_xml",
        "summary": summary,
        "message_type": message_type,
        "patient_information": patient_info,
        "medication_details": medication_info,
        "prescriber_information": prescriber_info,
        "pharmacy_information": pharmacy_info,
        "parsed_data": parsed
    }


# --------------------------------------------------------------------------- #
# NCPDP Parser Activity
# --------------------------------------------------------------------------- #

async def process_ncpdp_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Parse NCPDP message (auto-detects format: Telecommunication D.0 or SCRIPT XML)
    """
    config = activity.get("config", {})

    logger.info("=" * 80)
    logger.info("NCPDP PARSER START")
    logger.info(f"Config: {config}")
    logger.info(f"Context variables keys: {list(context.variables.keys())}")
    logger.info(f"Context raw_message type: {type(context.raw_message)}")
    logger.info(f"Context raw_message preview: {str(context.raw_message)[:200]}")

    payload = _resolve_payload_from_context(context, config, ["ncpdp_payload", "ncpdp_message"])

    logger.info(f"Resolved payload type: {type(payload)}")
    logger.info(f"Resolved payload preview: {str(payload)[:500]}")

    if not payload:
        logger.error("No NCPDP payload available")
        return _build_result(
            ActivityStatus.FAILED,
            "NCPDP parser failed",
            {},
            error="No NCPDP payload available"
        )

    payload_str = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
    logger.info(f"Payload string length: {len(payload_str)}")
    logger.info(f"Payload string preview: {payload_str[:500]}")
    logger.info("=" * 80)

    # Detect format
    format_type = _detect_ncpdp_format(payload_str)

    # Parse based on format
    if format_type == "script_xml":
        parsed = _parse_script_xml(payload_str)
    elif format_type == "telecommunication":
        parsed = _parse_telecommunication_segments(payload_str)
    else:
        return _build_result(
            ActivityStatus.FAILED,
            "NCPDP parser failed",
            {},
            error=f"Unknown NCPDP format. Could not detect telecommunication or XML format."
        )

    if isinstance(parsed, dict) and parsed.get("error"):
        return _build_result(
            ActivityStatus.FAILED,
            "NCPDP parser failed",
            parsed,
            error=parsed.get("error")
        )

    # Check if user provided explicit variable extraction configuration
    variables_config = config.get("variables", [])
    extraction_rules = _safe_json_loads(config.get("extraction_rules")) or config.get("extraction_rules")

    # If user provided explicit configuration, only extract those variables
    # Otherwise, use automatic extraction for convenience
    if variables_config or extraction_rules:
        extracted_variables: Dict[str, Any] = {}

        # Process variables config (old style, from UI)
        if isinstance(variables_config, list):
            for var_def in variables_config:
                var_name = var_def.get("name")
                var_source = var_def.get("source")
                var_default = var_def.get("default", "")

                if var_name and var_source:
                    # Map common field names to actual parsed data paths
                    value = None
                    source_upper = var_source.upper()

                    # Try to find the value in parsed data
                    if source_upper in ["RX_NUMBER", "PRESCRIPTION_NUMBER"]:
                        value = parsed.get("claim", {}).get("prescription_number", var_default)
                    elif source_upper == "PATIENT_ID":
                        value = parsed.get("insurance", {}).get("cardholder_id", var_default)
                    elif source_upper in ["DRUG_NAME", "PRODUCT_ID", "NDC"]:
                        value = parsed.get("claim", {}).get("product_id", var_default)
                    elif source_upper == "QUANTITY":
                        value = parsed.get("claim", {}).get("quantity_dispensed", var_default)
                    else:
                        # Try direct field lookup in raw_fields
                        value = parsed.get("raw_fields", {}).get(var_source, var_default)

                    extracted_variables[var_name] = value if value is not None else var_default

        # Process extraction rules (new style, dot notation paths)
        if isinstance(extraction_rules, list):
            for rule in extraction_rules:
                name = rule.get("name")
                path = rule.get("path")
                if name and path:
                    value = _extract_nested_value(parsed, path)
                    if value is not None:
                        extracted_variables[name] = value
    else:
        # No explicit configuration - use automatic extraction
        extracted_variables = _extract_ncpdp_variables(parsed, format_type)

    store_as = config.get("store_parsed_as", "ncpdp_message")
    _set_context_variable(context, store_as, parsed)
    _set_context_variable(context, "ncpdp_format", format_type)

    # Set all extracted variables in context
    context.variables.update(extracted_variables)

    return _build_result(
        ActivityStatus.COMPLETED,
        f"NCPDP message parsed ({format_type})",
        {
            "parsed_data": parsed,
            "format": format_type,
            "field_count": parsed.get("field_count", 0),
            "parsed_fields": parsed.get("raw_fields", {}),
            "extracted_variables": extracted_variables
        },
        variables={store_as: parsed, "ncpdp_format": format_type, **extracted_variables}
    )


# --------------------------------------------------------------------------- #
# NCPDP Transformation
# --------------------------------------------------------------------------- #

async def process_ncpdp_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Transform NCPDP message using rules"""
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


# --------------------------------------------------------------------------- #
# NCPDP Translation
# --------------------------------------------------------------------------- #

async def process_ncpdp_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Translate NCPDP message to readable English"""
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

    message = _normalize_parsed_wrapper(message)

    target_format = (config.get("target_format") or "english").lower()
    store_as = config.get("store_result_as", f"ncpdp_{target_format}")

    if target_format == "english":
        # Detect format from message
        msg_format = message.get("format", "")

        if msg_format == "script_xml":
            translated = _translate_script_xml_to_english(message)
        elif msg_format == "telecommunication_d0":
            translated = _translate_telecommunication_to_english(message)
        else:
            # Try to detect from message structure
            if "message_type" in message and ("patient" in message or "medication" in message):
                translated = _translate_script_xml_to_english(message)
            elif "segments" in message or "raw_fields" in message:
                translated = _translate_telecommunication_to_english(message)
            else:
                return _build_result(
                    ActivityStatus.FAILED,
                    "NCPDP translation failed",
                    {},
                    error="Could not determine NCPDP format for translation"
                )
    else:
        translated = message if target_format == "json" else json.dumps(message)

    _set_context_variable(context, store_as, translated)

    return _build_result(
        ActivityStatus.COMPLETED,
        "NCPDP message translated",
        translated,
        variables={store_as: translated}
    )


# --------------------------------------------------------------------------- #
# NCPDP Sender
# --------------------------------------------------------------------------- #

async def process_ncpdp_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Send NCPDP message to endpoint (supports both telecommunication and XML)"""
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

    # Determine format and protocol
    ncpdp_format = context.variables.get("ncpdp_format", "telecommunication")

    # Set appropriate transport protocol
    if ncpdp_format == "script_xml":
        transport = config.get("transport_protocol", "https")
        content_type = "application/xml"
    else:
        transport = config.get("transport_protocol", "tcp")
        content_type = "application/x-ncpdp"

    simulate = config.get("simulate", True)

    if not simulate and transport in {"http", "https"}:
        # Actual HTTP/HTTPS sending for XML format
        try:
            import httpx

            # Prepare payload
            if isinstance(payload, dict):
                if ncpdp_format == "script_xml":
                    # Extract raw XML if available
                    send_payload = payload.get("raw_xml", "")
                    if not send_payload:
                        send_payload = json.dumps(payload)
                        content_type = "application/json"
                else:
                    send_payload = json.dumps(payload)
                    content_type = "application/json"
            else:
                send_payload = str(payload)

            response = await httpx.AsyncClient(timeout=30).post(
                endpoint,
                content=send_payload,
                headers={"Content-Type": content_type},
            )
            success = 200 <= response.status_code < 300
            return _build_result(
                ActivityStatus.COMPLETED if success else ActivityStatus.FAILED,
                "NCPDP sender executed",
                {
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "transport": transport,
                    "format": ncpdp_format
                },
                variables={"ncpdp_sender_status": response.status_code},
                error=None if success else f"HTTP {response.status_code}"
            )
        except Exception as exc:
            return _build_result(
                ActivityStatus.FAILED,
                "NCPDP sender failed",
                {"endpoint": endpoint, "transport": transport, "format": ncpdp_format},
                error=str(exc)
            )

    # Simulation path (default)
    return _build_result(
        ActivityStatus.COMPLETED,
        "NCPDP sender simulated",
        {
            "endpoint": endpoint,
            "transport": transport,
            "format": ncpdp_format,
            "content_type": content_type,
            "simulated": True
        },
        variables={
            "ncpdp_sender_status": "SIMULATED",
            "ncpdp_payload_present": payload is not None,
            "ncpdp_format": ncpdp_format
        }
    )
