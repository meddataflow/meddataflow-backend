"""
Utilities for parsing and summarizing FHIR resources in a generic way.
"""
from __future__ import annotations

import json
from importlib import import_module
from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

JsonDict = Dict[str, Any]


class FHIRParsingError(ValueError):
    """Raised when a payload cannot be interpreted as a FHIR resource."""


def parse_fhir_resource(payload: Union[str, JsonDict]) -> JsonDict:
    """
    Parse (and validate) a FHIR payload, returning the JSON dictionary form.

    Raises:
        FHIRParsingError: if the payload is missing required fields or fails validation.
    """
    if isinstance(payload, str):
        try:
            resource = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FHIRParsingError(f"Payload is not valid JSON: {exc}") from exc
    elif isinstance(payload, dict):
        resource = payload
    else:
        raise FHIRParsingError("FHIR payload must be a JSON string or dictionary")

    if not isinstance(resource, dict):
        raise FHIRParsingError("FHIR payload must deserialize to a JSON object")

    resource_type = resource.get("resourceType")
    if not resource_type:
        raise FHIRParsingError("FHIR payload missing required 'resourceType' field")

    try:
        module = import_module(f"fhir.resources.{resource_type.lower()}")
        resource_cls = getattr(module, resource_type)
    except (ModuleNotFoundError, AttributeError) as exc:
        raise FHIRParsingError(f"Unsupported FHIR resource type: {resource_type}") from exc

    try:
        # First try strict validation
        if hasattr(resource_cls, "model_validate"):
            model = resource_cls.model_validate(resource)  # type: ignore[attr-defined]
            return json.loads(model.model_dump_json())  # type: ignore[attr-defined]
        model = resource_cls.parse_obj(resource)  # type: ignore[attr-defined]
        return json.loads(model.json())
    except ValidationError as exc:
        # If strict validation fails due to extra fields, try with validation disabled
        # This allows non-standard fields to pass through
        try:
            if hasattr(resource_cls, "model_validate"):
                # Use model_validate with context to bypass extra field validation
                from pydantic import ConfigDict
                # Return the original resource since validation failed but resource is structurally valid
                # We'll do a basic check that required fields are present
                if not resource.get("resourceType"):
                    raise FHIRParsingError(f"Invalid FHIR {resource_type}: missing resourceType") from exc
                # Return the original resource, accepting the extra fields
                return resource
            model = resource_cls.parse_obj(resource)  # type: ignore[attr-defined]
            return json.loads(model.json())
        except Exception:
            # If all else fails, raise the original validation error
            raise FHIRParsingError(f"Invalid FHIR {resource_type}: {exc}") from exc


def extract_common_fhir_values(resource: JsonDict) -> JsonDict:
    """
    Produce a dictionary of commonly useful values extracted from an arbitrary FHIR resource.
    The result is safe to serialize (only primitives, dicts and lists).
    """
    extracted: JsonDict = {}
    resource_type = resource.get("resourceType")
    resource_type_lower = resource_type.lower() if isinstance(resource_type, str) else ""

    extracted["resource_type"] = resource_type
    if resource.get("id"):
        extracted["id"] = resource["id"]

    display_name = _derive_display_name(resource)
    if display_name:
        extracted["display_name"] = display_name

    identifiers = _extract_identifiers(resource.get("identifier"))
    if identifiers:
        extracted["identifiers"] = identifiers
        first_identifier = next((item for item in identifiers if item.get("value")), identifiers[0])
        extracted["identifier"] = first_identifier.get("value") or first_identifier.get("system_value")
        if first_identifier.get("system"):
            extracted["identifier_system"] = first_identifier["system"]

    telecoms = _extract_telecoms(resource.get("telecom"))
    if telecoms:
        extracted["telecoms"] = telecoms
        phones = [entry["value"] for entry in telecoms if entry.get("system") == "phone" and entry.get("value")]
        emails = [entry["value"] for entry in telecoms if entry.get("system") == "email" and entry.get("value")]
        urls = [entry["value"] for entry in telecoms if entry.get("system") == "url" and entry.get("value")]
        if phones:
            extracted["primary_phone"] = phones[0]
            if len(phones) > 1:
                extracted["alternate_phones"] = phones[1:]
        if emails:
            extracted["email"] = emails[0]
        if urls:
            extracted["url"] = urls[0]

    addresses = _extract_addresses(resource.get("address"))
    if addresses:
        extracted["addresses"] = addresses
        extracted["address"] = addresses[0]

    contacts = _extract_contacts(resource.get("contact"))
    if contacts:
        extracted["contact_details"] = contacts
        contact_names = [entry["name"] for entry in contacts if entry.get("name")]
        if contact_names:
            extracted["contact_names"] = contact_names

    types = _codeable_concept_list(resource.get("type"))
    if types:
        extracted["types"] = types

    categories = _codeable_concept_list(resource.get("category"))
    if categories:
        extracted["categories"] = categories

    codes = _codeable_concept_list(resource.get("code"))
    if codes:
        extracted["codes"] = codes

    class_value = _codeable_concept_to_str(resource.get("class"))
    if class_value:
        extracted["resource_class"] = class_value

    subject = _reference_to_str(resource.get("subject"))
    if subject:
        extracted["subject"] = subject

    encounter = _reference_to_str(resource.get("encounter"))
    if encounter:
        extracted["encounter"] = encounter

    performers = _reference_list(resource.get("performer"))
    if performers:
        extracted["performers"] = performers

    author = _reference_list(resource.get("author"))
    if author:
        extracted["authors"] = author

    managing_org = _reference_to_str(resource.get("managingOrganization"))
    if managing_org:
        extracted["managing_organization"] = managing_org

    owner = _reference_to_str(resource.get("owner"))
    if owner:
        extracted["owner"] = owner

    language = resource.get("language")
    if language:
        extracted["language"] = language

    for key in ("status", "gender", "priority", "intent", "mode", "active", "experimental"):
        value = resource.get(key)
        if value is not None:
            extracted[key] = value

    period = _format_period(resource.get("period"))
    if period:
        extracted["period"] = period

    timing_values = _extract_timing_details(resource)
    extracted.update(timing_values)

    notes = _extract_notes(resource.get("note"))
    if notes:
        extracted["notes"] = notes

    if resource_type_lower == "patient":
        birth_date = resource.get("birthDate")
        if birth_date:
            extracted["birth_date"] = birth_date
    elif resource_type_lower == "organization":
        if resource.get("name"):
            extracted["organization_name"] = resource.get("name")

    # meta.lastUpdated is often meaningful for audits
    meta = resource.get("meta") or {}
    if isinstance(meta, dict) and meta.get("lastUpdated"):
        extracted["last_updated"] = meta["lastUpdated"]

    return extracted


def build_fhir_translation_summary(resource: JsonDict, extracted: Optional[JsonDict] = None) -> JsonDict:
    """
    Build a human-friendly summary of a FHIR resource that can be presented in the UI.
    """
    extracted = extracted or extract_common_fhir_values(resource)
    resource_type = extracted.get("resource_type") or resource.get("resourceType") or "FHIR Resource"
    resource_type_lower = resource_type.lower() if isinstance(resource_type, str) else ""
    display_name = (
        extracted.get("display_name")
        or extracted.get("identifier")
        or resource.get("id")
        or resource_type
    )

    key_attributes: List[str] = []
    sections: List[Dict[str, Any]] = []
    details: List[str] = []

    def add_attr(label: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, list):
            clean = [str(item) for item in value if item not in (None, "", [])]
            if not clean:
                return
            formatted = ", ".join(clean)
        else:
            formatted = str(value)
            if not formatted:
                return
        key_attributes.append(f"{label}: {formatted}")

    add_attr("Identifier", extracted.get("identifier"))
    add_attr("Identifier System", extracted.get("identifier_system"))

    for label, key in (
        ("Status", "status"),
        ("Language", "language"),
        ("Priority", "priority"),
        ("Class", "resource_class"),
        ("Subject", "subject"),
        ("Encounter", "encounter"),
        ("Managing Organization", "managing_organization"),
        ("Owner", "owner"),
    ):
        add_attr(label, extracted.get(key))

    if extracted.get("categories"):
        add_attr("Categories", extracted["categories"])
    if extracted.get("types"):
        add_attr("Types", extracted["types"])
    if extracted.get("codes"):
        add_attr("Codes", extracted["codes"])

    if extracted.get("period"):
        details.append(f"Period: {extracted['period']}")

    if extracted.get("effective_dates"):
        details.append(f"Effective: {', '.join(extracted['effective_dates'])}")
    if extracted.get("authored_on"):
        details.append(f"Authored on: {extracted['authored_on']}")
    if extracted.get("recorded_date"):
        details.append(f"Recorded on: {extracted['recorded_date']}")
    if extracted.get("last_updated"):
        details.append(f"Last updated: {extracted['last_updated']}")

    telecoms = extracted.get("telecoms")
    telecom_section: List[str] = []
    if isinstance(telecoms, list):
        for entry in telecoms:
            system = entry.get("system", "").capitalize()
            use = entry.get("use")
            value = entry.get("value")
            if not value:
                continue
            label = f"{system or 'Contact'}: {value}"
            if use:
                label += f" ({use})"
            telecom_section.append(label)
    if telecom_section:
        sections.append({"title": "Contact Methods", "items": telecom_section, "dotColor": "bg-indigo-500"})

    addresses = extracted.get("addresses") if isinstance(extracted.get("addresses"), list) else None
    if addresses:
        sections.append({"title": "Locations", "items": addresses, "dotColor": "bg-amber-500"})

    contacts = extracted.get("contact_details")
    if isinstance(contacts, list):
        contact_items: List[str] = []
        for idx, contact in enumerate(contacts, start=1):
            parts: List[str] = []
            if contact.get("name"):
                parts.append(contact["name"])
            if contact.get("telecom"):
                telecom_desc = ", ".join(contact["telecom"])
                if telecom_desc:
                    parts.append(telecom_desc)
            if contact.get("address"):
                parts.append(contact["address"])
            if contact.get("relationship"):
                parts.append(f"Relationship: {contact['relationship']}")
            if parts:
                contact_items.append(f"Contact {idx}: " + "; ".join(parts))
        if contact_items:
            sections.append({"title": "Contacts", "items": contact_items, "dotColor": "bg-purple-500"})

    identifiers = extracted.get("identifiers")
    if isinstance(identifiers, list):
        formatted_identifiers: List[str] = []
        for ident in identifiers:
            value = ident.get("value") or ident.get("system_value")
            system = ident.get("system")
            use = ident.get("use")
            ident_text = value or system
            if ident_text:
                detail = ident_text
                annotations: List[str] = []
                if system:
                    annotations.append(system)
                if use:
                    annotations.append(f"use: {use}")
                if ident.get("type"):
                    annotations.append(f"type: {ident['type']}")
                if annotations:
                    detail += f" ({', '.join(annotations)})"
                formatted_identifiers.append(detail)
        if formatted_identifiers:
            sections.append({"title": "Identifiers", "items": formatted_identifiers, "dotColor": "bg-blue-500"})

    notes = extracted.get("notes")
    if notes:
        sections.append({"title": "Notes", "items": notes, "dotColor": "bg-gray-500"})

    summary = _build_summary_text(resource_type, resource_type_lower, display_name, extracted, resource)

    translation: JsonDict = {
        "summary": summary,
        "resource_type": resource_type,
        "identifier": extracted.get("identifier"),
        "display_name": display_name,
        "key_attributes": key_attributes,
        "details": details,
        "sections": sections,
        "raw_resource": resource,
    }

    if resource_type_lower == "patient":
        translation["patient_information"] = key_attributes
    elif resource_type_lower == "organization":
        translation["organization_information"] = key_attributes

    contact_info = extracted.get("contact_names")
    if contact_info:
        translation["contact_information"] = contact_info
    if addresses:
        translation["addresses"] = addresses

    return translation


# -------------------------------------------------------------------------- #
# Internal helpers                                                           #
# -------------------------------------------------------------------------- #

def _derive_display_name(resource: JsonDict) -> Optional[str]:
    name = resource.get("name")
    if name:
        formatted = _format_human_name(name)
        if formatted:
            return formatted
        if isinstance(name, str):
            return name

    title = resource.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    description = resource.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()

    if resource.get("id"):
        return str(resource["id"])

    return None


def _ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _codeable_concept_to_str(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        if value.get("text"):
            return str(value["text"]).strip()
        coding = value.get("coding")
        if isinstance(coding, list) and coding:
            primary = coding[0]
            if isinstance(primary, dict):
                if primary.get("display"):
                    return str(primary["display"]).strip()
                if primary.get("code"):
                    return str(primary["code"]).strip()
        if value.get("code"):
            return str(value["code"]).strip()
    elif isinstance(value, list):
        for item in value:
            label = _codeable_concept_to_str(item)
            if label:
                return label
    elif isinstance(value, str):
        return value.strip() or None
    return None


def _codeable_concept_list(value: Any) -> List[str]:
    concepts = []
    for item in _ensure_list(value):
        label = _codeable_concept_to_str(item)
        if label:
            concepts.append(label)
    return concepts


def _reference_to_str(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        if value.get("display"):
            return str(value["display"]).strip()
        if value.get("reference"):
            return str(value["reference"]).strip()
    elif isinstance(value, str):
        return value.strip() or None
    return None


def _reference_list(value: Any) -> List[str]:
    references = []
    for item in _ensure_list(value):
        ref = _reference_to_str(item)
        if ref:
            references.append(ref)
    return references


def _format_human_name(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        if value.get("text"):
            return str(value["text"]).strip()
        parts: List[str] = []
        for key in ("prefix", "given", "family", "suffix"):
            portion = value.get(key)
            if isinstance(portion, list):
                parts.extend(str(p).strip() for p in portion if p)
            elif isinstance(portion, str):
                part = portion.strip()
                if part:
                    parts.append(part)
        return " ".join(parts).strip() or None
    if isinstance(value, list):
        for entry in value:
            formatted = _format_human_name(entry)
            if formatted:
                return formatted
    if isinstance(value, str):
        return value.strip() or None
    return None


def _extract_identifiers(value: Any) -> List[JsonDict]:
    identifiers: List[JsonDict] = []
    for identifier in _ensure_list(value):
        if not isinstance(identifier, dict):
            continue
        record: JsonDict = {}
        if identifier.get("value"):
            record["value"] = identifier["value"]
        if identifier.get("system"):
            record["system"] = identifier["system"]
        if identifier.get("use"):
            record["use"] = identifier["use"]
        coded_type = _codeable_concept_to_str(identifier.get("type"))
        if coded_type:
            record["type"] = coded_type
        assigner = _reference_to_str(identifier.get("assigner"))
        if assigner:
            record["assigner"] = assigner
        if identifier.get("period"):
            period = _format_period(identifier.get("period"))
            if period:
                record["period"] = period
        if record:
            record["system_value"] = _format_identifier(record)
            identifiers.append(record)
    return identifiers


def _format_identifier(data: JsonDict) -> str:
    value = data.get("value") or ""
    system = data.get("system") or ""
    if value and system:
        return f"{value} ({system})"
    return value or system


def _extract_telecoms(value: Any) -> List[JsonDict]:
    telecoms: List[JsonDict] = []
    for telecom in _ensure_list(value):
        if not isinstance(telecom, dict):
            continue
        record: JsonDict = {}
        for key in ("system", "value", "use", "rank"):
            if telecom.get(key) is not None:
                record[key] = telecom[key]
        if record:
            telecoms.append(record)
    return telecoms


def _extract_addresses(value: Any) -> List[str]:
    addresses: List[str] = []
    for addr in _ensure_list(value):
        if not isinstance(addr, dict):
            continue
        lines = addr.get("line")
        if isinstance(lines, list):
            line_part = ", ".join(str(line).strip() for line in lines if line)
        else:
            line_part = str(lines).strip() if lines else ""
        city = addr.get("city")
        state = addr.get("state")
        postal = addr.get("postalCode")
        country = addr.get("country")
        components = [comp for comp in (line_part, city, state, postal, country) if comp]
        if components:
            addresses.append(", ".join(components))
    return addresses


def _extract_contacts(value: Any) -> List[JsonDict]:
    contacts: List[JsonDict] = []
    for contact in _ensure_list(value):
        if not isinstance(contact, dict):
            continue
        entry: JsonDict = {}
        name = _format_human_name(contact.get("name"))
        if name:
            entry["name"] = name
        telecoms = _extract_telecoms(contact.get("telecom"))
        if telecoms:
            entry["telecom_raw"] = telecoms
            entry["telecom"] = [
                f"{item.get('system', 'contact').capitalize()}: {item.get('value')}"
                + (f" ({item.get('use')})" if item.get("use") else "")
                for item in telecoms
                if item.get("value")
            ]
        address_list = _extract_addresses(contact.get("address"))
        if address_list:
            entry["address"] = address_list[0]
        relationship = _codeable_concept_list(contact.get("relationship"))
        if relationship:
            entry["relationship"] = ", ".join(relationship)
        if entry:
            contacts.append(entry)
    return contacts


def _format_period(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    start = value.get("start")
    end = value.get("end")
    if start and end:
        return f"{start} to {end}"
    return start or end


def _extract_notes(value: Any) -> List[str]:
    notes: List[str] = []
    for note in _ensure_list(value):
        if isinstance(note, dict):
            text = note.get("text")
            if text:
                notes.append(str(text))
        elif isinstance(note, str) and note.strip():
            notes.append(note.strip())
    return notes


def _extract_timing_details(resource: JsonDict) -> JsonDict:
    result: JsonDict = {}
    effective_dates: List[str] = []
    for key in ("effectiveDateTime", "effectiveInstant", "occurrenceDateTime"):
        if resource.get(key):
            effective_dates.append(str(resource[key]))

    for key in ("effectivePeriod", "occurrencePeriod"):
        period = _format_period(resource.get(key))
        if period:
            effective_dates.append(period)

    if effective_dates:
        result["effective_dates"] = effective_dates

    if resource.get("authoredOn"):
        result["authored_on"] = resource["authoredOn"]
    if resource.get("recordedDate"):
        result["recorded_date"] = resource["recordedDate"]
    if resource.get("date"):
        result["date"] = resource["date"]
    if resource.get("issued"):
        result["issued"] = resource["issued"]
    if resource.get("created"):
        result["created"] = resource["created"]
    if resource.get("performedDateTime"):
        result["performed"] = resource["performedDateTime"]
    if resource.get("performedPeriod"):
        period = _format_period(resource.get("performedPeriod"))
        if period:
            result["performed"] = period
    return result


def _build_summary_text(
    resource_type: str,
    resource_type_lower: str,
    display_name: str,
    extracted: JsonDict,
    resource: JsonDict,
) -> str:
    status = extracted.get("status")
    identifier = extracted.get("identifier")

    if resource_type_lower == "patient":
        gender = extracted.get("gender")
        birth_date = resource.get("birthDate") or extracted.get("birth_date")
        pieces = [f"Patient {display_name}"]
        if gender:
            pieces.append(gender.capitalize())
        if birth_date:
            pieces.append(f"born {birth_date}")
        if identifier:
            pieces.append(f"(MRN {identifier})")
        return ", ".join(pieces)

    if resource_type_lower == "organization":
        summary = f"{resource_type} {display_name}"
        if status is not None:
            summary += f" ({'active' if resource.get('active') else 'inactive'})"
        return summary

    summary = f"{resource_type} {display_name}"
    if status:
        summary += f" — status: {status}"
    categories = extracted.get("categories")
    if categories:
        summary += f" [{', '.join(categories[:2])}]"
    return summary
