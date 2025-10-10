"""
X12 EDI Message Processor
Handles parsing, transformation, translation, and sending of X12 messages.
"""
import json
import logging
from typing import Any, Dict, Iterable, Optional

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
            logger.debug("x12_processor: failed to parse JSON, using raw text")
    return value


def _resolve_payload_from_context(
    context: WorkflowContext,
    config: Dict[str, Any],
    default_keys: Iterable[str],
) -> Optional[Any]:
    """Resolve X12 payload from workflow context"""
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
        # Simple path extraction - can be enhanced
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


# --------------------------------------------------------------------------- #
# X12 Parsing
# --------------------------------------------------------------------------- #

def _parse_x12_segments(payload: str) -> Dict[str, Any]:
    """
    Parse X12 message into segments.
    Handles both standard (~) and newline-separated formats.
    """
    payload = payload.strip()

    # Determine segment terminator
    if "~" in payload:
        segments = payload.split("~")
    else:
        segments = payload.split("\n")

    parsed: Dict[str, Any] = {}
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        elements = segment.split("*")
        if len(elements) == 0:
            continue

        tag = elements[0].strip()
        if not tag:
            continue

        parsed.setdefault(tag, []).append(elements[1:])

    return parsed


async def process_x12_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Parse X12 message into structured segments"""
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

    store_as = config.get("store_parsed_as", "x12_message")
    _set_context_variable(context, store_as, parsed)

    return _build_result(
        ActivityStatus.COMPLETED,
        "X12 payload parsed",
        {"segment_count": len(parsed), "segments": parsed},
        variables={store_as: parsed}
    )


# --------------------------------------------------------------------------- #
# X12 Transformation
# --------------------------------------------------------------------------- #

async def process_x12_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Transform X12 message using rules"""
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


# --------------------------------------------------------------------------- #
# X12 Translation to English
# --------------------------------------------------------------------------- #

def _translate_x12_to_english(message: Dict[str, Any]) -> Dict[str, Any]:
    """Translate X12 parsed segments into human-readable English"""

    # Determine transaction type
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

    # Extract ISA
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

    # Extract GS
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

    details = []
    line_items = []

    # Parse 850 (Purchase Order)
    if transaction_code == "850":
        # BEG segment
        if "BEG" in message and len(message["BEG"]) > 0:
            beg = message["BEG"][0]
            purpose_codes = {"00": "Original", "01": "Cancellation", "04": "Change", "05": "Replace"}
            purpose = purpose_codes.get(beg[0] if len(beg) > 0 else "", beg[0] if len(beg) > 0 else "")
            po_type_codes = {"SA": "Stand-alone Order", "KN": "Purchase Order", "NE": "New Order"}
            po_type = po_type_codes.get(beg[1] if len(beg) > 1 else "", beg[1] if len(beg) > 1 else "")
            po_number = beg[2] if len(beg) > 2 else ""
            po_date = beg[4] if len(beg) > 4 else ""

            details.append(f"Purchase Order Type: {purpose} {po_type}")
            if po_number:
                details.append(f"PO Number: {po_number}")
            if po_date and len(po_date) == 8:
                formatted_date = f"{po_date[0:4]}-{po_date[4:6]}-{po_date[6:8]}"
                details.append(f"PO Date: {formatted_date}")

        # REF segments
        if "REF" in message:
            for ref in message["REF"]:
                if len(ref) >= 2:
                    ref_codes = {"DP": "Department Number", "PS": "Purchase String", "PO": "Purchase Order Number", "IV": "Invoice Number"}
                    ref_type = ref_codes.get(ref[0], ref[0])
                    ref_value = ref[1] if len(ref) > 1 and ref[1] else "Not specified"
                    details.append(f"{ref_type}: {ref_value}")

        # ITD - Payment Terms
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

        # DTM - Dates
        if "DTM" in message:
            for dtm in message["DTM"]:
                if len(dtm) >= 2:
                    date_codes = {"001": "Cancel After", "002": "Delivery Requested", "010": "Ship Not Before", "063": "Do Not Deliver After"}
                    date_type = date_codes.get(dtm[0], f"Date ({dtm[0]})")
                    date_val = dtm[1] if len(dtm) > 1 else ""
                    if date_val and len(date_val) == 8:
                        formatted = f"{date_val[0:4]}-{date_val[4:6]}-{date_val[6:8]}"
                        details.append(f"{date_type}: {formatted}")

        # N1/N3/N4 - Parties
        if "N1" in message:
            for idx, n1 in enumerate(message["N1"]):
                if len(n1) >= 1:
                    party_codes = {"ST": "Ship To", "BT": "Bill To", "BY": "Buyer", "SE": "Seller", "VN": "Vendor"}
                    party_type = party_codes.get(n1[0], n1[0])
                    party_name = n1[1] if len(n1) > 1 else ""
                    party_id = n1[3] if len(n1) > 3 else ""

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

                    details.append(party_info)

        # PO1 - Line Items
        if "PO1" in message:
            for idx, po1 in enumerate(message["PO1"], 1):
                if len(po1) >= 3:
                    line_num = po1[0] if len(po1) > 0 else str(idx)
                    quantity = po1[1] if len(po1) > 1 else ""
                    unit = po1[2] if len(po1) > 2 else ""
                    price = po1[3] if len(po1) > 3 else ""
                    product_id = po1[6] if len(po1) > 6 else ""
                    vendor_part = po1[9] if len(po1) > 9 else ""

                    unit_names = {"EA": "each", "CA": "case", "BX": "box", "DZ": "dozen", "LB": "pound", "KG": "kilogram"}
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
        details.append(f"Healthcare transaction {transaction_code}")
        details.append("Detailed parsing available for 850 (Purchase Orders)")

    return {
        "summary": summary,
        "transaction_type": transaction_type,
        "transaction_code": transaction_code,
        "details": details,
        "line_items": line_items,
        "interchange_info": isa_info,
        "group_info": gs_info,
        "raw_segments": message
    }


async def process_x12_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Translate X12 message to target format (English, JSON, etc.)"""
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


# --------------------------------------------------------------------------- #
# X12 Sender
# --------------------------------------------------------------------------- #

async def process_x12_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Send X12 message to endpoint (simulated)"""
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
