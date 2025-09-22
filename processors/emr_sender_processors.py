"""
EMR Sender Activity Processors
Handles sending HL7/FHIR data to various EMR systems
"""
import json
import logging
from typing import Dict, List, Optional, Any
import httpx
from datetime import datetime

from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus

logger = logging.getLogger(__name__)


async def process_ecw_fhir_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Send FHIR data to eClinicalWorks EMR system
    Supports patient creation, updates, and other FHIR resources
    """
    try:
        config = activity.get("config", {})

        # Extract configuration
        base_url = config.get("base_url", "").rstrip("/")
        oauth_token = config.get("oauth_token", "")
        resource_type = config.get("resource_type", "Patient")
        operation = config.get("operation", "create")
        field_mappings = config.get("field_mappings", [])
        timeout_seconds = config.get("timeout_seconds", 30)

        if not base_url or not oauth_token:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="eClinicalWorks base_url and oauth_token are required"
            )

        # Build FHIR resource from context variables and mappings
        fhir_resource = _build_fhir_resource(resource_type, context.variables, field_mappings)

        # Prepare request headers
        headers = {
            "Authorization": f"Bearer {oauth_token}",
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json"
        }

        # Determine endpoint and method
        if operation == "create":
            endpoint = f"{base_url}/{resource_type}"
            method = "POST"
        elif operation == "update":
            resource_id = context.variables.get("resource_id") or fhir_resource.get("id")
            if not resource_id:
                return ActivityResult(
                    status=ActivityStatus.FAILED,
                    error_message="Resource ID is required for update operation"
                )
            endpoint = f"{base_url}/{resource_type}/{resource_id}"
            method = "PUT"
        else:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=f"Unsupported operation: {operation}"
            )


        # Send HTTP request
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method=method,
                url=endpoint,
                headers=headers,
                json=fhir_resource
            )

        # Handle response
        if response.status_code in [200, 201]:
            response_data = response.json() if response.content else {}
            resource_id = response_data.get("id", "")

            return ActivityResult(
                status=ActivityStatus.COMPLETED,
                output_data={
                    "message": f"Successfully sent {resource_type} to eClinicalWorks",
                    "response": response_data,
                    "resource_id": resource_id,
                    "status_code": response.status_code
                },
                variables={
                    "ecw_resource_id": resource_id,
                    "ecw_response": response_data
                }
            )
        else:
            error_msg = f"eClinicalWorks API error: {response.status_code} - {response.text}"
            logger.error(error_msg)
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=error_msg
            )

    except Exception as e:
        logger.error(f"Error in eClinicalWorks FHIR sender: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to send data to eClinicalWorks: {str(e)}"
        )


async def process_nextgen_api_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Send data to NextGen Healthcare via Enterprise APIs
    Supports 800+ API endpoints for comprehensive integration
    """
    try:
        config = activity.get("config", {})

        # Extract configuration
        base_url = config.get("base_url", "").rstrip("/")
        api_key = config.get("api_key", "")
        endpoint = config.get("endpoint", "")
        http_method = config.get("http_method", "POST")
        field_mappings = config.get("field_mappings", [])
        custom_headers = config.get("custom_headers", {})
        timeout_seconds = config.get("timeout_seconds", 30)

        if not base_url or not api_key or not endpoint:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="NextGen base_url, api_key, and endpoint are required"
            )

        # Build payload from context variables and mappings
        payload = _build_nextgen_payload(context.variables, field_mappings)

        # Prepare request headers
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            **custom_headers
        }

        # Build full URL
        full_url = f"{base_url}{endpoint}"


        # Send HTTP request
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method=http_method,
                url=full_url,
                headers=headers,
                json=payload if http_method in ["POST", "PUT", "PATCH"] else None,
                params=payload if http_method == "GET" else None
            )

        # Handle response
        if response.status_code in [200, 201, 202]:
            response_data = response.json() if response.content else {}
            record_id = response_data.get("id", "") or response_data.get("recordId", "")

            return ActivityResult(
                status=ActivityStatus.COMPLETED,
                output_data={
                    "message": f"Successfully sent data to NextGen Healthcare",
                    "response": response_data,
                    "record_id": record_id,
                    "status_code": response.status_code
                },
                variables={
                    "nextgen_record_id": record_id,
                    "nextgen_response": response_data
                }
            )
        else:
            error_msg = f"NextGen API error: {response.status_code} - {response.text}"
            logger.error(error_msg)
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=error_msg
            )

    except Exception as e:
        logger.error(f"Error in NextGen API sender: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to send data to NextGen Healthcare: {str(e)}"
        )


async def process_cerner_fhir_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Send FHIR data to Oracle Health (formerly Cerner) EMR system
    Supports FHIR R4 patient and clinical data integration
    """
    try:
        config = activity.get("config", {})

        # Extract configuration
        base_url = config.get("base_url", "").rstrip("/")
        oauth_token = config.get("oauth_token", "")
        resource_type = config.get("resource_type", "Patient")
        operation = config.get("operation", "create")
        field_mappings = config.get("field_mappings", [])
        timeout_seconds = config.get("timeout_seconds", 30)

        if not base_url or not oauth_token:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="Oracle Health base_url and oauth_token are required"
            )

        # Build FHIR resource from context variables and mappings
        fhir_resource = _build_fhir_resource(resource_type, context.variables, field_mappings)

        # Prepare request headers
        headers = {
            "Authorization": f"Bearer {oauth_token}",
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json"
        }

        # Determine endpoint and method
        if operation == "create":
            endpoint = f"{base_url}/fhir/r4/{resource_type}"
            method = "POST"
        elif operation == "update":
            resource_id = context.variables.get("resource_id") or fhir_resource.get("id")
            if not resource_id:
                return ActivityResult(
                    status=ActivityStatus.FAILED,
                    error_message="Resource ID is required for update operation"
                )
            endpoint = f"{base_url}/fhir/r4/{resource_type}/{resource_id}"
            method = "PUT"
        else:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=f"Unsupported operation: {operation}"
            )


        # Send HTTP request
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method=method,
                url=endpoint,
                headers=headers,
                json=fhir_resource
            )

        # Handle response
        if response.status_code in [200, 201]:
            response_data = response.json() if response.content else {}
            resource_id = response_data.get("id", "")

            return ActivityResult(
                status=ActivityStatus.COMPLETED,
                output_data={
                    "message": f"Successfully sent {resource_type} to Oracle Health",
                    "response": response_data,
                    "resource_id": resource_id,
                    "status_code": response.status_code
                },
                variables={
                    "cerner_resource_id": resource_id,
                    "cerner_response": response_data
                }
            )
        else:
            error_msg = f"Oracle Health API error: {response.status_code} - {response.text}"
            logger.error(error_msg)
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=error_msg
            )

    except Exception as e:
        logger.error(f"Error in Oracle Health FHIR sender: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to send data to Oracle Health: {str(e)}"
        )


async def process_epic_hl7_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """
    Send HL7 data to Epic EMR system
    Uses Epic's inbound HL7 interfaces for patient administration and encounters
    """
    try:
        config = activity.get("config", {})

        # Extract configuration
        hl7_endpoint = config.get("hl7_endpoint", "")
        message_type = config.get("message_type", "ADT^A04")
        sending_application = config.get("sending_application", "MEDDATAFLOW")
        receiving_application = config.get("receiving_application", "EPIC")
        field_mappings = config.get("field_mappings", [])
        timeout_seconds = config.get("timeout_seconds", 30)

        if not hl7_endpoint:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="Epic HL7 endpoint is required"
            )

        # Build HL7 message from context variables and mappings
        hl7_message = _build_hl7_message(
            message_type,
            sending_application,
            receiving_application,
            context.variables,
            field_mappings
        )


        # Send HL7 message (implementation depends on Epic's interface type)
        if hl7_endpoint.startswith("http"):
            # HTTP-based HL7 interface
            headers = {
                "Content-Type": "application/hl7-v2",
                "Accept": "application/hl7-v2"
            }

            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    url=hl7_endpoint,
                    headers=headers,
                    content=hl7_message
                )

            if response.status_code in [200, 202]:
                return ActivityResult(
                    status=ActivityStatus.COMPLETED,
                    output_data={
                        "message": f"Successfully sent HL7 message to Epic",
                        "hl7_message": hl7_message,
                        "status_code": response.status_code,
                        "response": response.text
                    },
                    variables={
                        "epic_hl7_sent": True,
                        "epic_response": response.text
                    }
                )
            else:
                error_msg = f"Epic HL7 interface error: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return ActivityResult(
                    status=ActivityStatus.FAILED,
                    error_message=error_msg
                )
        else:
            # TCP/Socket-based HL7 interface (placeholder for complex implementation)
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="TCP-based HL7 interfaces not yet implemented. Use HTTP endpoint."
            )

    except Exception as e:
        logger.error(f"Error in Epic HL7 sender: {e}")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Failed to send HL7 message to Epic: {str(e)}"
        )


# Helper functions

def _build_fhir_resource(resource_type: str, variables: Dict[str, Any], field_mappings: List[Dict]) -> Dict[str, Any]:
    """Build FHIR resource from context variables and field mappings"""
    resource = {
        "resourceType": resource_type,
        "id": variables.get("resource_id", "")
    }

    # Apply field mappings
    for mapping in field_mappings:
        source_field = mapping.get("source_field", "")
        target_field = mapping.get("target_field", "")
        default_value = mapping.get("default_value", "")

        if source_field and target_field:
            value = variables.get(source_field, default_value)
            _set_nested_field(resource, target_field, value)

    # Default Patient resource structure if not mapped
    if resource_type == "Patient" and not field_mappings:
        resource.update({
            "active": True,
            "name": [{
                "family": variables.get("PATIENT_LAST_NAME", ""),
                "given": [variables.get("PATIENT_FIRST_NAME", "")]
            }],
            "identifier": [{
                "value": variables.get("PATIENT_ID", ""),
                "type": {"text": "MR"}
            }]
        })

    return resource


def _build_nextgen_payload(variables: Dict[str, Any], field_mappings: List[Dict]) -> Dict[str, Any]:
    """Build NextGen API payload from context variables and field mappings"""
    payload = {}

    # Apply field mappings
    for mapping in field_mappings:
        source_field = mapping.get("source_field", "")
        target_field = mapping.get("target_field", "")
        default_value = mapping.get("default_value", "")

        if source_field and target_field:
            value = variables.get(source_field, default_value)
            payload[target_field] = value

    # Default payload structure if not mapped
    if not field_mappings:
        payload = {
            "patientId": variables.get("PATIENT_ID", ""),
            "lastName": variables.get("PATIENT_LAST_NAME", ""),
            "firstName": variables.get("PATIENT_FIRST_NAME", "")
        }

    return payload


def _build_hl7_message(message_type: str, sending_app: str, receiving_app: str,
                      variables: Dict[str, Any], field_mappings: List[Dict]) -> str:
    """Build HL7 message from context variables"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    control_id = f"MSG{timestamp}"

    # MSH segment
    msh = f"MSH|^~\\&|{sending_app}|SENDING_FAC|{receiving_app}|RECEIVING_FAC|{timestamp}||{message_type}|{control_id}|P|2.5|||"

    # PID segment
    patient_id = variables.get("PATIENT_ID", "")
    last_name = variables.get("PATIENT_LAST_NAME", "")
    first_name = variables.get("PATIENT_FIRST_NAME", "")

    pid = f"PID|1||{patient_id}^^^MR||{last_name}^{first_name}^||||||||||||||||||||||||||"

    # Combine segments
    hl7_message = f"{msh}\r\n{pid}"

    return hl7_message


def _set_nested_field(obj: Dict[str, Any], field_path: str, value: Any):
    """Set nested field in dictionary using dot notation (e.g., 'name.0.family')"""
    parts = field_path.split('.')
    current = obj

    for part in parts[:-1]:
        if part.isdigit():
            part = int(part)
            if not isinstance(current, list):
                current = []
            while len(current) <= part:
                current.append({})
            current = current[part]
        else:
            if part not in current:
                current[part] = {}
            current = current[part]

    final_key = parts[-1]
    if final_key.isdigit():
        final_key = int(final_key)
        if not isinstance(current, list):
            current = []
        while len(current) <= final_key:
            current.append("")
        current[final_key] = value
    else:
        current[final_key] = value