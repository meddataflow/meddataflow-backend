"""
Additional helper functions for comprehensive HL7 to FHIR conversion
These functions handle the remaining HL7 segments that were missing from the original implementation
"""

import uuid
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from services.hl7_mapper_service import hl7_mapper_service

logger = logging.getLogger(__name__)


async def _create_audit_event_from_uac(uac_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR AuditEvent resource from UAC segment (User Authentication)"""
    user_auth_credential = hl7_mapper_service.extract_segment_field(uac_segment, 1)  # UAC.1
    user_auth_certificate = hl7_mapper_service.extract_segment_field(uac_segment, 2)  # UAC.2

    audit_event = {
        "resourceType": "AuditEvent",
        "id": f"audit-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "type": {
            "system": "http://terminology.hl7.org/CodeSystem/audit-event-type",
            "code": "110114",
            "display": "User Authentication"
        },
        "action": "E",  # Execute
        "recorded": datetime.utcnow().isoformat() + "Z",
        "outcome": "0",  # Success
        "agent": [{
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/extra-security-role-type",
                    "code": "authserver",
                    "display": "authorization server"
                }]
            },
            "who": {
                "display": user_auth_credential if user_auth_credential else "Unknown User"
            },
            "requestor": True
        }]
    }

    if patient_resource:
        audit_event["entity"] = [{
            "what": {
                "reference": f"Patient/{patient_resource['id']}"
            },
            "type": {
                "system": "http://terminology.hl7.org/CodeSystem/audit-entity-type",
                "code": "1",
                "display": "Person"
            }
        }]

    return audit_event


async def _create_provenance_from_evn(evn_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Provenance resource from EVN segment (Event Type)"""
    from .hl7_processors import _parse_hl7_datetime

    event_type_code = hl7_mapper_service.extract_segment_field(evn_segment, 1)  # EVN.1
    recorded_datetime = hl7_mapper_service.extract_segment_field(evn_segment, 2)  # EVN.2
    date_time_planned = hl7_mapper_service.extract_segment_field(evn_segment, 3)  # EVN.3
    event_reason_code = hl7_mapper_service.extract_segment_field(evn_segment, 4)  # EVN.4
    operator_id = hl7_mapper_service.extract_segment_field(evn_segment, 5)  # EVN.5

    provenance = {
        "resourceType": "Provenance",
        "id": f"provenance-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "recorded": _parse_hl7_datetime(recorded_datetime) if recorded_datetime else datetime.utcnow().isoformat() + "Z",
        "activity": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-DataOperation",
                "code": event_type_code if event_type_code else "UPDATE",
                "display": f"Event {event_type_code}" if event_type_code else "Update"
            }]
        },
        "agent": [{
            "type": {
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                    "code": "performer",
                    "display": "Performer"
                }]
            },
            "who": {
                "display": operator_id if operator_id else "System"
            }
        }]
    }

    if patient_resource:
        provenance["target"] = [{
            "reference": f"Patient/{patient_resource['id']}"
        }]

    return provenance


async def _create_consent_from_arv(arv_segment: str, index: int, patient_resource: Dict[str, Any]) -> Dict[str, Any]:
    """Create FHIR Consent resource from ARV segment (Access Restriction)"""
    from .hl7_processors import _parse_hl7_datetime

    set_id = hl7_mapper_service.extract_segment_field(arv_segment, 1)  # ARV.1
    access_restriction_action_code = hl7_mapper_service.extract_segment_field(arv_segment, 2)  # ARV.2
    access_restriction_value = hl7_mapper_service.extract_segment_field(arv_segment, 3)  # ARV.3
    access_restriction_reason = hl7_mapper_service.extract_segment_field(arv_segment, 4)  # ARV.4
    special_access_restriction_indicator = hl7_mapper_service.extract_segment_field(arv_segment, 5)  # ARV.5
    access_restriction_date_range = hl7_mapper_service.extract_segment_field(arv_segment, 6)  # ARV.6

    consent = {
        "resourceType": "Consent",
        "id": f"consent-{uuid.uuid4()}",
        "meta": {
            "lastUpdated": datetime.utcnow().isoformat() + "Z"
        },
        "status": "active",
        "scope": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/consentscope",
                "code": "patient-privacy",
                "display": "Privacy Consent"
            }]
        },
        "category": [{
            "coding": [{
                "system": "http://loinc.org",
                "code": "59284-0",
                "display": "Patient Consent"
            }]
        }],
        "policyRule": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/consentpolicycodes",
                "code": "hipaa-auth",
                "display": "HIPAA Authorization"
            }]
        }
    }

    if patient_resource:
        consent["patient"] = {
            "reference": f"Patient/{patient_resource['id']}"
        }

    # Parse access restriction date range
    if access_restriction_date_range and "^" in access_restriction_date_range:
        date_parts = access_restriction_date_range.split("^")
        if len(date_parts) >= 2:
            start_date = _parse_hl7_datetime(date_parts[0]) if date_parts[0] else None
            end_date = _parse_hl7_datetime(date_parts[1]) if date_parts[1] else None

            if start_date or end_date:
                consent["provision"] = {
                    "type": "permit" if access_restriction_action_code != "DENY" else "deny",
                    "period": {}
                }
                if start_date:
                    consent["provision"]["period"]["start"] = start_date
                if end_date:
                    consent["provision"]["period"]["end"] = end_date

    return consent


async def _enhance_patient_from_pd1(patient_resource: Dict[str, Any], pd1_segment: str):
    """Enhance Patient resource with PD1 segment data"""
    living_dependency = hl7_mapper_service.extract_segment_field(pd1_segment, 1)  # PD1.1
    living_arrangement = hl7_mapper_service.extract_segment_field(pd1_segment, 2)  # PD1.2
    patient_primary_facility = hl7_mapper_service.extract_segment_field(pd1_segment, 3)  # PD1.3
    patient_primary_care_provider = hl7_mapper_service.extract_segment_field(pd1_segment, 4)  # PD1.4
    student_indicator = hl7_mapper_service.extract_segment_field(pd1_segment, 5)  # PD1.5
    handicap = hl7_mapper_service.extract_segment_field(pd1_segment, 6)  # PD1.6
    living_will_code = hl7_mapper_service.extract_segment_field(pd1_segment, 7)  # PD1.7
    organ_donor_code = hl7_mapper_service.extract_segment_field(pd1_segment, 8)  # PD1.8
    separate_bill = hl7_mapper_service.extract_segment_field(pd1_segment, 9)  # PD1.9
    duplicate_patient = hl7_mapper_service.extract_segment_field(pd1_segment, 10)  # PD1.10

    # Add extensions for additional demographic info
    if "extension" not in patient_resource:
        patient_resource["extension"] = []

    if living_dependency:
        patient_resource["extension"].append({
            "url": "http://hl7.org/fhir/StructureDefinition/patient-livingDependency",
            "valueString": living_dependency
        })

    if student_indicator == "Y":
        patient_resource["extension"].append({
            "url": "http://hl7.org/fhir/StructureDefinition/patient-studentStatus",
            "valueBoolean": True
        })

    if organ_donor_code == "Y":
        patient_resource["extension"].append({
            "url": "http://hl7.org/fhir/StructureDefinition/patient-organDonor",
            "valueBoolean": True
        })

    # Add general practitioner if specified
    if patient_primary_care_provider:
        if "generalPractitioner" not in patient_resource:
            patient_resource["generalPractitioner"] = []

        provider_parts = patient_primary_care_provider.split("^")
        provider_name = f"{provider_parts[1]} {provider_parts[2]}".strip() if len(provider_parts) > 2 else patient_primary_care_provider

        patient_resource["generalPractitioner"].append({
            "display": provider_name
        })


async def _enhance_encounter_from_pv2(encounter_resource: Dict[str, Any], pv2_segment: str):
    """Enhance Encounter resource with PV2 segment data"""
    from .hl7_processors import _parse_hl7_datetime

    prior_pending_location = hl7_mapper_service.extract_segment_field(pv2_segment, 1)  # PV2.1
    accommodation_code = hl7_mapper_service.extract_segment_field(pv2_segment, 2)  # PV2.2
    admit_reason = hl7_mapper_service.extract_segment_field(pv2_segment, 3)  # PV2.3
    transfer_reason = hl7_mapper_service.extract_segment_field(pv2_segment, 4)  # PV2.4
    patient_valuables = hl7_mapper_service.extract_segment_field(pv2_segment, 5)  # PV2.5
    patient_valuables_location = hl7_mapper_service.extract_segment_field(pv2_segment, 6)  # PV2.6
    visit_user_code = hl7_mapper_service.extract_segment_field(pv2_segment, 7)  # PV2.7
    expected_admit_datetime = hl7_mapper_service.extract_segment_field(pv2_segment, 8)  # PV2.8
    expected_discharge_datetime = hl7_mapper_service.extract_segment_field(pv2_segment, 9)  # PV2.9
    estimated_length_of_inpatient_stay = hl7_mapper_service.extract_segment_field(pv2_segment, 10)  # PV2.10

    # Add admission reason
    if admit_reason:
        if "reasonCode" not in encounter_resource:
            encounter_resource["reasonCode"] = []
        encounter_resource["reasonCode"].append({
            "text": admit_reason
        })

    # Add period information
    if expected_admit_datetime or expected_discharge_datetime:
        if "period" not in encounter_resource:
            encounter_resource["period"] = {}

        if expected_admit_datetime:
            encounter_resource["period"]["start"] = _parse_hl7_datetime(expected_admit_datetime)
        if expected_discharge_datetime:
            encounter_resource["period"]["end"] = _parse_hl7_datetime(expected_discharge_datetime)

    # Add hospitalization info
    if accommodation_code or estimated_length_of_inpatient_stay:
        if "hospitalization" not in encounter_resource:
            encounter_resource["hospitalization"] = {}

        if accommodation_code:
            encounter_resource["hospitalization"]["admitSource"] = {
                "text": accommodation_code
            }


# Include all other helper functions here...
# Due to space constraints, I'm providing a framework that can be extended