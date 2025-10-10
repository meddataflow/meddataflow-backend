"""
Clinical Document Processor (CDA, CCD, CCR)
Handles parsing, transformation, translation, and sending of HL7 CDA, CCD, and CCR documents.
"""
import json
import logging
from typing import Any, Dict, Iterable, Optional
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
            logger.debug("clinical_document_processor: failed to parse JSON, using raw text")
    return value


def _resolve_payload_from_context(
    context: WorkflowContext,
    config: Dict[str, Any],
    default_keys: Iterable[str],
) -> Optional[Any]:
    """Resolve clinical document payload from workflow context"""
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


# --------------------------------------------------------------------------- #
# XML Parsing Functions
# --------------------------------------------------------------------------- #

def _parse_xml_document(payload: str) -> ElementTree.Element:
    """Parse XML document string into ElementTree"""
    return ElementTree.fromstring(payload)


def _extract_cda_summary(root: ElementTree.Element) -> Dict[str, Any]:
    """Extract a structured summary from a CDA document"""
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
            sections.append({
                "title": _text(section.find("cda:title", namespaces=ns)),
                "code": section.find("cda:code", namespaces=ns).get("code") if section.find("cda:code", namespaces=ns) is not None else None,
                "code_system": section.find("cda:code", namespaces=ns).get("codeSystem") if section.find("cda:code", namespaces=ns) is not None else None,
                "code_display_name": section.find("cda:code", namespaces=ns).get("displayName") if section.find("cda:code", namespaces=ns) is not None else None,
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
        authors.append({
            "time": _find_attr(".//cda:time", "value"),
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
    """Extract a structured summary from a CCR document"""
    ns = {"ccr": "urn:astm-org:CCR"}

    def _text(element: Optional[ElementTree.Element]) -> Optional[str]:
        if element is None:
            return None
        text = "".join(element.itertext()).strip()
        return text or None

    def _find_text(path: str) -> Optional[str]:
        element = root.find(path, namespaces=ns)
        return _text(element)

    def _collect_actors() -> Dict[str, Dict[str, Any]]:
        actors: Dict[str, Dict[str, Any]] = {}
        for actor in root.findall(".//ccr:Actors/ccr:Actor", namespaces=ns):
            actor_id = _text(actor.find("ccr:ActorObjectID", namespaces=ns))
            if not actor_id:
                continue
            person = actor.find("ccr:Person", namespaces=ns)
            organization = actor.find("ccr:Organization", namespaces=ns)
            actor_entry: Dict[str, Any] = {}
            if person is not None:
                name = person.find("ccr:Name/ccr:CurrentName", namespaces=ns)
                if name is not None:
                    given = _text(name.find("ccr:Given", namespaces=ns))
                    family = _text(name.find("ccr:Family", namespaces=ns))
                    actor_entry["name"] = " ".join(part for part in [given, family] if part)
                ids = []
                for id_elem in person.findall("ccr:IDs", namespaces=ns):
                    id_value = _text(id_elem.find("ccr:ID", namespaces=ns))
                    id_type = _text(id_elem.find("ccr:Type/ccr:Text", namespaces=ns))
                    if id_value:
                        ids.append({"type": id_type, "value": id_value})
                if ids:
                    actor_entry["ids"] = ids
            if organization is not None:
                actor_entry["organization"] = _text(organization.find("ccr:Name", namespaces=ns))
            if actor_entry:
                actors[actor_id] = actor_entry
        return actors

    actors = _collect_actors()

    # Patient information
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

    # Author/provider - use actors other than patient
    authors = []
    from_actor_id = _text(root.find("ccr:From/ccr:ActorID", namespaces=ns))
    if from_actor_id and from_actor_id in actors:
        author_entry = actors[from_actor_id]
        authors.append({
            "name": author_entry.get("name"),
            "organization": author_entry.get("organization"),
        })
    # Additional provider actors
    for actor_id, info in actors.items():
        if actor_id in {from_actor_id, patient.get("id")}:
            continue
        if info.get("organization") or info.get("name"):
            authors.append({
                "name": info.get("name"),
                "organization": info.get("organization"),
            })

    sections = []

    def _collect_section_items(tag: str, title: str) -> None:
        entries = []
        for item in root.findall(f".//ccr:Body/ccr:{tag}/ccr:{tag[:-1]}", namespaces=ns):
            description = _text(item.find("ccr:Description/ccr:Text", namespaces=ns))
            code_value = _text(item.find("ccr:Code/ccr:Value", namespaces=ns))
            code_system = _text(item.find("ccr:Code/ccr:CodingSystem", namespaces=ns))
            if description:
                text = description
                if code_value:
                    text += f" ({code_value}"
                    if code_system:
                        text += f" {code_system}"
                    text += ")"
                entries.append(text)
        if entries:
            sections.append({
                "title": title,
                "text": "; ".join(entries)
            })

    def _collect_medications():
        entries = []
        for med in root.findall(".//ccr:Body/ccr:Medications/ccr:Medication", namespaces=ns):
            product_name = _text(med.find("ccr:Product/ccr:ProductName/ccr:Text", namespaces=ns))
            direction = _text(med.find("ccr:Directions/ccr:Direction/ccr:Text", namespaces=ns))
            status = _text(med.find("ccr:Status/ccr:Text", namespaces=ns))
            parts = [part for part in [product_name, direction, status] if part]
            if parts:
                entries.append(" — ".join(parts))
        if entries:
            sections.append({
                "title": "Medications",
                "text": "; ".join(entries)
            })

    def _collect_results():
        entries = []
        for result in root.findall(".//ccr:Body/ccr:Results/ccr:Result", namespaces=ns):
            description = _text(result.find("ccr:Description/ccr:Text", namespaces=ns))
            value = _text(result.find("ccr:Value/ccr:Text", namespaces=ns))
            units = _text(result.find("ccr:Value/ccr:Units", namespaces=ns))
            date = _text(result.find("ccr:Test/ccr:DateTime/ccr:ExactDateTime", namespaces=ns))
            parts = [part for part in [description, value, units, date] if part]
            if parts:
                entries.append(" ".join(parts))
        if entries:
            sections.append({
                "title": "Results",
                "text": "; ".join(entries)
            })

    _collect_section_items("Problems", "Problems")
    _collect_section_items("Alerts", "Alerts")
    _collect_medications()
    _collect_results()

    summary = {
        "title": _find_text(".//ccr:Title/ccr:Text"),
        "effective_time": _find_text(".//ccr:DateTime/ccr:ExactDateTime"),
        "document_code": {
            "code": _find_text(".//ccr:CCRDocumentObjectID"),
            "code_system": None,
            "display_name": "Continuity of Care Record"
        },
        "confidentiality_code": None,
        "language_code": _find_text(".//ccr:Language/ccr:Text"),
        "patient": {k: v for k, v in patient.items() if v} if patient else None,
        "authors": [author for author in authors if any(author.values())],
        "custodian": {"id": None, "name": actors.get(from_actor_id, {}).get("organization") if from_actor_id else None},
        "sections": sections
    }
    return summary


def _extract_clinical_document_summary(root: ElementTree.Element) -> Dict[str, Any]:
    tag = root.tag.split("}")[-1]
    if tag == "ClinicalDocument":
        return _extract_cda_summary(root)
    if tag == "ContinuityOfCareRecord":
        return _extract_ccr_summary(root)
    return {}


def _build_clinical_document_narrative(summary: Dict[str, Any]) -> str:
    """Create a human-readable narrative from a clinical document summary."""
    lines: list[str] = []

    title = summary.get("title")
    if title:
        lines.append(f"Document: {title}")

    doc_code = summary.get("document_code") or {}
    display_name = doc_code.get("display_name")
    code = doc_code.get("code")
    if display_name or code:
        if code:
            lines.append(f"Type: {display_name or 'Document'} (code {code})")
        else:
            lines.append(f"Type: {display_name}")

    effective_time = summary.get("effective_time")
    if effective_time:
        lines.append(f"Effective Date: {effective_time}")

    patient = summary.get("patient") or {}
    patient_parts = []
    if patient.get("full_name"):
        patient_parts.append(patient["full_name"])
    elif patient.get("given_name") or patient.get("family_name"):
        patient_parts.append(f"{patient.get('given_name', '')} {patient.get('family_name', '')}".strip())
    if patient.get("gender"):
        patient_parts.append(patient["gender"])
    if patient.get("birth_time"):
        patient_parts.append(f"DOB {patient['birth_time']}")
    if patient_parts:
        lines.append(f"Patient: {', '.join(filter(None, patient_parts))}")
    if patient.get("telecom"):
        lines.append(f"Contact: {patient['telecom']}")
    if patient.get("address"):
        lines.append(f"Address: {patient['address']}")

    authors = summary.get("authors") or []
    if authors:
        author_strings = []
        for author in authors:
            pieces = []
            if author.get("name"):
                pieces.append(author["name"])
            if author.get("organization"):
                pieces.append(author["organization"])
            if pieces:
                author_strings.append(" / ".join(pieces))
        if author_strings:
            lines.append(f"Author(s): {', '.join(author_strings)}")

    custodian = summary.get("custodian") or {}
    if custodian.get("name"):
        lines.append(f"Custodian: {custodian['name']}")

    sections = summary.get("sections") or []
    for section in sections:
        if not section:
            continue
        title = section.get("title") or section.get("code_display_name") or "Section"
        text = section.get("text")
        if text:
            lines.append(f"{title}: {text}")

    return "\n".join(lines)


def _build_clinical_document_translation(
    summary: Dict[str, Any],
    document: str,
    target_format: str
) -> Dict[str, Any]:
    """Build a translation payload for clinical documents."""
    narrative = _build_clinical_document_narrative(summary)

    patient_information: list[str] = []
    patient = summary.get("patient") or {}
    if patient.get("full_name"):
        patient_information.append(f"Name: {patient['full_name']}")
    elif patient.get("given_name") or patient.get("family_name"):
        patient_information.append(f"Name: {patient.get('given_name', '')} {patient.get('family_name', '')}".strip())
    if patient.get("gender"):
        patient_information.append(f"Gender: {patient['gender']}")
    if patient.get("birth_time"):
        patient_information.append(f"Birth Date: {patient['birth_time']}")
    if patient.get("telecom"):
        patient_information.append(f"Contact: {patient['telecom']}")
    if patient.get("address"):
        patient_information.append(f"Address: {patient['address']}")

    details: list[str] = []
    for section in summary.get("sections") or []:
        if not section:
            continue
        title = section.get("title") or section.get("code_display_name") or "Section"
        text = section.get("text")
        if text:
            details.append(f"{title}: {text}")

    document_metadata = {}
    doc_code = (summary.get("document_code") or {}).copy()
    if doc_code:
        document_metadata["Document Code"] = ", ".join(
            filter(None, [
                doc_code.get("display_name"),
                doc_code.get("code"),
                doc_code.get("code_system")
            ])
        )
    if summary.get("confidentiality_code"):
        document_metadata["Confidentiality"] = summary["confidentiality_code"]
    if summary.get("language_code"):
        document_metadata["Language"] = summary["language_code"]

    if summary.get("authors"):
        author_strings = []
        for author in summary["authors"]:
            pieces = []
            if author.get("name"):
                pieces.append(author["name"])
            if author.get("organization"):
                pieces.append(author["organization"])
            if author.get("time"):
                pieces.append(f"{author['time']}")
            if pieces:
                author_strings.append(" / ".join(pieces))
        if author_strings:
            document_metadata["Author(s)"] = "; ".join(author_strings)

    if summary.get("custodian", {}).get("name"):
        document_metadata["Custodian"] = summary["custodian"]["name"]

    translated = {
        "summary": narrative,
        "patient_information": patient_information,
        "details": details,
        "document_metadata": document_metadata,
        "document": document,
        "metadata": summary,
        "target_format": target_format,
    }
    return translated


# --------------------------------------------------------------------------- #
# Generic Clinical Document Processor
# --------------------------------------------------------------------------- #

async def _process_clinical_document_parser(
    activity: Dict[str, Any],
    context: WorkflowContext,
    document_type: str,
) -> ActivityResult:
    """Parse clinical document (CDA, CCD, CCR)"""
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
    store_as = config.get("store_parsed_as", f"{document_type}_document")
    parsed_document = {"document": payload_str, "summary": summary}
    _set_context_variable(context, store_as, parsed_document)
    _set_context_variable(context, f"{document_type}_summary", summary)

    return _build_result(
        ActivityStatus.COMPLETED,
        f"{document_type.upper()} document parsed",
        {"summary": summary},
        variables={store_as: parsed_document, f"{document_type}_summary": summary}
    )


async def _process_clinical_document_transformer(
    activity: Dict[str, Any],
    context: WorkflowContext,
    document_type: str,
) -> ActivityResult:
    """Transform clinical document using rules"""
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
    """Translate clinical document to target format"""
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
    """Send clinical document to endpoint (simulated)"""
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


# --------------------------------------------------------------------------- #
# CDA (Clinical Document Architecture) Activities
# --------------------------------------------------------------------------- #

async def process_cda_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Parse CDA document"""
    return await _process_clinical_document_parser(activity, context, "cda")


async def process_cda_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Transform CDA document"""
    return await _process_clinical_document_transformer(activity, context, "cda")


async def process_cda_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Translate CDA document"""
    return await _process_clinical_document_translator(activity, context, "cda")


async def process_cda_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Send CDA document"""
    return await _process_clinical_document_sender(activity, context, "cda")


# --------------------------------------------------------------------------- #
# CCD (Continuity of Care Document) Activities
# --------------------------------------------------------------------------- #

async def process_ccd_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Parse CCD document"""
    return await _process_clinical_document_parser(activity, context, "ccd")


async def process_ccd_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Transform CCD document"""
    return await _process_clinical_document_transformer(activity, context, "ccd")


async def process_ccd_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Translate CCD document"""
    return await _process_clinical_document_translator(activity, context, "ccd")


async def process_ccd_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Send CCD document"""
    return await _process_clinical_document_sender(activity, context, "ccd")


# --------------------------------------------------------------------------- #
# CCR (Continuity of Care Record) Activities
# --------------------------------------------------------------------------- #

async def process_ccr_parser_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Parse CCR document"""
    return await _process_clinical_document_parser(activity, context, "ccr")


async def process_ccr_transformer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Transform CCR document"""
    return await _process_clinical_document_transformer(activity, context, "ccr")


async def process_ccr_translator_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Translate CCR document"""
    return await _process_clinical_document_translator(activity, context, "ccr")


async def process_ccr_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Send CCR document"""
    return await _process_clinical_document_sender(activity, context, "ccr")
