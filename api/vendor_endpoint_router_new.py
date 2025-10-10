"""
Vendor Endpoint Router - AsyncPG Compatible
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import json

from models.vendor_endpoint import VendorEndpointRepository
from models.hl7_message import HL7MessageRepository
from services.workflow_execution_service import workflow_execution_service
from services.hl7_parser import HL7Parser
from api.auth_deps import get_current_user, get_current_tenant

router = APIRouter(prefix="/api/vendor-endpoints", tags=["vendor-endpoints"])
hl7_parser = HL7Parser()

def _normalize_list_field(value) -> List[str]:
    """Coerce DB-returned JSON/JSONB/text representations to a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        try:
            import json as _json
            parsed = _json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v is not None]
        except Exception:
            pass
        # Fallback: split comma-separated string
        return [s.strip() for s in value.split(',') if s.strip()]
    # Unknown type
    return []

SUPPORTED_MESSAGE_FORMATS = {
    "hl7",
    "fhir",
    "dicom",
    "ncpdp",
    "x12",
    "cda",
    "ccd",
    "ccr",
    "terminology"
}

MESSAGE_FORMAT_ALIASES = {
    "hl7_v2": "hl7",
    "hl7v2": "hl7",
    "hl7": "hl7",
    "fhir_json": "fhir",
    "json_fhir": "fhir",
    "fhir": "fhir",
    "dicomweb": "dicom",
    "dicom": "dicom",
    "ncpdp": "ncpdp",
    "script": "ncpdp",
    "x12": "x12",
    "edi": "x12",
    "cda": "cda",
    "ccd": "ccd",
    "ccr": "ccr",
    "terminology": "terminology",
    "codes": "terminology",
    "json": "terminology",
    "xml": "cda"
}


def normalize_message_format(value: Optional[str]) -> str:
    raw = (value or "hl7").strip().lower()
    normalized = MESSAGE_FORMAT_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_MESSAGE_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported message format '{value}'. Supported values: {', '.join(sorted(SUPPORTED_MESSAGE_FORMATS))}"
        )
    return normalized


def safe_normalize_message_format(value: Optional[str]) -> str:
    try:
        return normalize_message_format(value)
    except HTTPException:
        return "hl7"

# Pydantic models
class VendorEndpointStatistics(BaseModel):
    total_received: int
    total_processed: int
    total_failed: int
    success_rate: float

class VendorEndpointResponse(BaseModel):
    id: str
    vendor_slug: str
    vendor_name: str
    vendor_description: Optional[str] = None
    vendor_contact_email: Optional[str] = None
    vendor_contact_phone: Optional[str] = None
    api_key: Optional[str] = None
    message_format: str
    max_message_size: int
    rate_limit_per_hour: int
    is_active: bool
    require_ssl: bool
    ignored_message_types: Optional[List[str]] = []
    total_messages_received: int
    total_messages_processed: int
    total_messages_failed: int
    statistics: VendorEndpointStatistics
    created_at: datetime
    updated_at: datetime
    trigger_workflow_id: Optional[str] = None
    trigger_workflow_name: Optional[str] = None
    ack_on_receive: Optional[bool] = False
    ack_profile: Optional[str] = None

class VendorEndpointCreate(BaseModel):
    vendor_slug: str
    vendor_name: str
    vendor_description: Optional[str] = None
    vendor_contact_email: Optional[EmailStr] = None
    vendor_contact_phone: Optional[str] = None
    message_format: str = "hl7"
    max_message_size: int = 10485760  # 10MB
    rate_limit_per_hour: int = 1000

class VendorEndpointUpdate(BaseModel):
    vendor_name: Optional[str] = None
    vendor_description: Optional[str] = None
    vendor_contact_email: Optional[EmailStr] = None
    vendor_contact_phone: Optional[str] = None
    message_format: Optional[str] = None
    max_message_size: Optional[int] = None
    rate_limit_per_hour: Optional[int] = None
    is_active: Optional[bool] = None
    require_ssl: Optional[bool] = None
    trigger_workflow_id: Optional[str] = None
    ignored_message_types: Optional[List[str]] = None
    ack_on_receive: Optional[bool] = None
    ack_profile: Optional[str] = None

def calculate_endpoint_statistics(endpoint):
    """Calculate endpoint statistics for frontend display"""
    total_received = endpoint.get('total_messages_received', 0)
    total_processed = endpoint.get('total_messages_processed', 0)
    total_failed = endpoint.get('total_messages_failed', 0)
    
    success_rate = 0.0
    if total_received > 0:
        success_rate = (total_processed / total_received) * 100
    
    return VendorEndpointStatistics(
        total_received=total_received,
        total_processed=total_processed,
        total_failed=total_failed,
        success_rate=round(success_rate, 2)
    )

@router.get("/", response_model=List[VendorEndpointResponse])
async def get_vendor_endpoints(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    is_active: Optional[bool] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get vendor endpoints for current tenant"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        endpoints = await VendorEndpointRepository.get_endpoints_by_tenant(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            is_active=is_active
        )
        
        return [
            VendorEndpointResponse(
                id=str(ep['id']),
                vendor_slug=ep['vendor_slug'],
                vendor_name=ep['vendor_name'],
                vendor_description=ep.get('vendor_description'),
                vendor_contact_email=ep.get('vendor_contact_email'),
                vendor_contact_phone=ep.get('vendor_contact_phone'),
                api_key=ep.get('api_key'),
                message_format=safe_normalize_message_format(ep.get('message_format')),
                max_message_size=ep['max_message_size'],
                rate_limit_per_hour=ep['rate_limit_per_hour'],
                is_active=ep['is_active'],
                require_ssl=ep['require_ssl'],
                ack_on_receive=ep.get('ack_on_receive', False),
                ack_profile=ep.get('ack_profile'),
                ignored_message_types=_normalize_list_field(ep.get('ignored_message_types')),
                total_messages_received=ep.get('computed_total_messages') or ep.get('total_messages_received', 0),
                total_messages_processed=ep.get('computed_processed_messages') or ep.get('total_messages_processed', 0),
                total_messages_failed=ep.get('computed_failed_messages') or ep.get('total_messages_failed', 0),
                statistics=calculate_endpoint_statistics(ep),
                created_at=ep['created_at'],
                updated_at=ep['updated_at'],
                trigger_workflow_id=str(ep['trigger_workflow_id']) if ep.get('trigger_workflow_id') else None,
                trigger_workflow_name=ep.get('trigger_workflow_name')
            )
            for ep in endpoints
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch vendor endpoints: {str(e)}"
        )

@router.get("/{endpoint_id}", response_model=VendorEndpointResponse)
async def get_vendor_endpoint(
    endpoint_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get a specific vendor endpoint"""
    try:
        endpoint_uuid = uuid.UUID(endpoint_id)
        endpoint = await VendorEndpointRepository.get_endpoint_by_id(endpoint_uuid)
        
        if not endpoint:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )
            
        # Check if endpoint belongs to current tenant
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        if endpoint['tenant_id'] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )
            
        normalized_format = safe_normalize_message_format(endpoint.get('message_format'))

        return VendorEndpointResponse(
            id=str(endpoint['id']),
            vendor_slug=endpoint['vendor_slug'],
            vendor_name=endpoint['vendor_name'],
            vendor_description=endpoint.get('vendor_description'),
            vendor_contact_email=endpoint.get('vendor_contact_email'),
            vendor_contact_phone=endpoint.get('vendor_contact_phone'),
            api_key=endpoint.get('api_key'),
            message_format=normalized_format,
            max_message_size=endpoint['max_message_size'],
            rate_limit_per_hour=endpoint['rate_limit_per_hour'],
            is_active=endpoint['is_active'],
            require_ssl=endpoint['require_ssl'],
            ack_on_receive=endpoint.get('ack_on_receive', False),
            ack_profile=endpoint.get('ack_profile'),
            ignored_message_types=_normalize_list_field(endpoint.get('ignored_message_types')),
            total_messages_received=endpoint.get('computed_total_messages') or endpoint.get('total_messages_received', 0),
            total_messages_processed=endpoint.get('computed_processed_messages') or endpoint.get('total_messages_processed', 0),
            total_messages_failed=endpoint.get('computed_failed_messages') or endpoint.get('total_messages_failed', 0),
            statistics=calculate_endpoint_statistics({
                **endpoint,
                'total_messages_received': endpoint.get('computed_total_messages') or endpoint.get('total_messages_received', 0),
                'total_messages_processed': endpoint.get('computed_processed_messages') or endpoint.get('total_messages_processed', 0),
                'total_messages_failed': endpoint.get('computed_failed_messages') or endpoint.get('total_messages_failed', 0),
            }),
            created_at=endpoint['created_at'],
            updated_at=endpoint['updated_at'],
            trigger_workflow_id=str(endpoint['trigger_workflow_id']) if endpoint.get('trigger_workflow_id') else None,
            trigger_workflow_name=endpoint.get('trigger_workflow_name')
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid endpoint ID format"
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch vendor endpoint: {str(e)}"
        )

@router.post("/", response_model=VendorEndpointResponse)
async def create_vendor_endpoint(
    endpoint_data: VendorEndpointCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Create a new vendor endpoint"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        normalized_format = normalize_message_format(endpoint_data.message_format)

        # Check if slug already exists for this tenant
        if not await VendorEndpointRepository.validate_slug(tenant_id, endpoint_data.vendor_slug):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vendor slug already exists for this tenant"
            )
            
        endpoint = await VendorEndpointRepository.create_endpoint(
            tenant_id=tenant_id,
            vendor_slug=endpoint_data.vendor_slug,
            vendor_name=endpoint_data.vendor_name,
            vendor_description=endpoint_data.vendor_description,
            vendor_contact_email=str(endpoint_data.vendor_contact_email) if endpoint_data.vendor_contact_email else None,
            vendor_contact_phone=endpoint_data.vendor_contact_phone,
            api_key=str(uuid.uuid4()),
            message_format=normalized_format,
            max_message_size=endpoint_data.max_message_size,
            rate_limit_per_hour=endpoint_data.rate_limit_per_hour
        )

        return VendorEndpointResponse(
            id=str(endpoint['id']),
            vendor_slug=endpoint['vendor_slug'],
            vendor_name=endpoint['vendor_name'],
            vendor_description=endpoint.get('vendor_description'),
            vendor_contact_email=endpoint.get('vendor_contact_email'),
            vendor_contact_phone=endpoint.get('vendor_contact_phone'),
            api_key=endpoint.get('api_key'),
            message_format=normalized_format,
            max_message_size=endpoint['max_message_size'],
            rate_limit_per_hour=endpoint['rate_limit_per_hour'],
            is_active=endpoint['is_active'],
            require_ssl=endpoint['require_ssl'],
            ack_on_receive=endpoint.get('ack_on_receive', False),
            ack_profile=endpoint.get('ack_profile'),
            ignored_message_types=_normalize_list_field(endpoint.get('ignored_message_types')),
            total_messages_received=endpoint.get('computed_total_messages') or endpoint.get('total_messages_received', 0),
            total_messages_processed=endpoint.get('computed_processed_messages') or endpoint.get('total_messages_processed', 0),
            total_messages_failed=endpoint.get('computed_failed_messages') or endpoint.get('total_messages_failed', 0),
            statistics=calculate_endpoint_statistics({
                **endpoint,
                'total_messages_received': endpoint.get('computed_total_messages') or endpoint.get('total_messages_received', 0),
                'total_messages_processed': endpoint.get('computed_processed_messages') or endpoint.get('total_messages_processed', 0),
                'total_messages_failed': endpoint.get('computed_failed_messages') or endpoint.get('total_messages_failed', 0),
            }),
            created_at=endpoint['created_at'],
            updated_at=endpoint['updated_at'],
            trigger_workflow_id=str(endpoint['trigger_workflow_id']) if endpoint.get('trigger_workflow_id') else None,
            trigger_workflow_name=endpoint.get('trigger_workflow_name')
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create vendor endpoint: {str(e)}"
        )

@router.put("/{endpoint_id}", response_model=VendorEndpointResponse)
async def update_vendor_endpoint(
    endpoint_id: str,
    endpoint_data: VendorEndpointUpdate,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Update a vendor endpoint"""
    try:
        endpoint_uuid = uuid.UUID(endpoint_id)
        
        # First check if endpoint exists and belongs to tenant
        existing_endpoint = await VendorEndpointRepository.get_endpoint_by_id(endpoint_uuid)
        if not existing_endpoint:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )
        
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        if existing_endpoint['tenant_id'] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )
        
        # Prepare update data
        update_data = {}
        for field, value in endpoint_data.dict(exclude_unset=True).items():
            if value is not None:
                if field == 'vendor_contact_email' and value:
                    update_data[field] = str(value)
                else:
                    update_data[field] = value
            
        endpoint = await VendorEndpointRepository.update_endpoint(endpoint_uuid, **update_data)

        normalized_format = safe_normalize_message_format(endpoint.get('message_format'))

        return VendorEndpointResponse(
            id=str(endpoint['id']),
            vendor_slug=endpoint['vendor_slug'],
            vendor_name=endpoint['vendor_name'],
            vendor_description=endpoint.get('vendor_description'),
            vendor_contact_email=endpoint.get('vendor_contact_email'),
            vendor_contact_phone=endpoint.get('vendor_contact_phone'),
            api_key=endpoint.get('api_key'),
            message_format=normalized_format,
            max_message_size=endpoint['max_message_size'],
            rate_limit_per_hour=endpoint['rate_limit_per_hour'],
            is_active=endpoint['is_active'],
            require_ssl=endpoint['require_ssl'],
            ignored_message_types=_normalize_list_field(endpoint.get('ignored_message_types')),
            total_messages_received=endpoint.get('computed_total_messages') or endpoint.get('total_messages_received', 0),
            total_messages_processed=endpoint.get('computed_processed_messages') or endpoint.get('total_messages_processed', 0),
            total_messages_failed=endpoint.get('computed_failed_messages') or endpoint.get('total_messages_failed', 0),
            statistics=calculate_endpoint_statistics({
                **endpoint,
                'total_messages_received': endpoint.get('computed_total_messages') or endpoint.get('total_messages_received', 0),
                'total_messages_processed': endpoint.get('computed_processed_messages') or endpoint.get('total_messages_processed', 0),
                'total_messages_failed': endpoint.get('computed_failed_messages') or endpoint.get('total_messages_failed', 0),
            }),
            created_at=endpoint['created_at'],
            updated_at=endpoint['updated_at'],
            trigger_workflow_id=str(endpoint['trigger_workflow_id']) if endpoint.get('trigger_workflow_id') else None,
            trigger_workflow_name=endpoint.get('trigger_workflow_name')
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid endpoint ID format"
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update vendor endpoint: {str(e)}"
        )

@router.delete("/{endpoint_id}")
async def delete_vendor_endpoint(
    endpoint_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Delete a vendor endpoint"""
    try:
        endpoint_uuid = uuid.UUID(endpoint_id)
        
        # First check if endpoint exists and belongs to tenant
        existing_endpoint = await VendorEndpointRepository.get_endpoint_by_id(endpoint_uuid)
        if not existing_endpoint:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )
        
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        if existing_endpoint['tenant_id'] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )
        
        await VendorEndpointRepository.delete_endpoint(endpoint_uuid)
        
        return {"message": "Vendor endpoint deleted successfully"}
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid endpoint ID format"
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete vendor endpoint: {str(e)}"
        )

@router.post("/{endpoint_id}/regenerate-api-key", response_model=VendorEndpointResponse)
async def regenerate_api_key(
    endpoint_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Regenerate API key for a vendor endpoint"""
    try:
        endpoint_uuid = uuid.UUID(endpoint_id)

        # First check if endpoint exists and belongs to tenant
        existing_endpoint = await VendorEndpointRepository.get_endpoint_by_id(endpoint_uuid)
        if not existing_endpoint:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )

        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)

        if existing_endpoint['tenant_id'] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )

        # Generate new API key
        new_api_key = str(uuid.uuid4())

        # Update with new API key
        endpoint = await VendorEndpointRepository.update_endpoint(endpoint_uuid, api_key=new_api_key)

        normalized_format = safe_normalize_message_format(endpoint.get('message_format'))

        return VendorEndpointResponse(
            id=str(endpoint['id']),
            vendor_slug=endpoint['vendor_slug'],
            vendor_name=endpoint['vendor_name'],
            vendor_description=endpoint.get('vendor_description'),
            vendor_contact_email=endpoint.get('vendor_contact_email'),
            vendor_contact_phone=endpoint.get('vendor_contact_phone'),
            api_key=endpoint.get('api_key'),
            message_format=normalized_format,
            max_message_size=endpoint['max_message_size'],
            rate_limit_per_hour=endpoint['rate_limit_per_hour'],
            is_active=endpoint['is_active'],
            require_ssl=endpoint['require_ssl'],
            total_messages_received=endpoint.get('total_messages_received', 0),
            total_messages_processed=endpoint.get('total_messages_processed', 0),
            total_messages_failed=endpoint.get('total_messages_failed', 0),
            statistics=calculate_endpoint_statistics(endpoint),
            created_at=endpoint['created_at'],
            updated_at=endpoint['updated_at'],
            trigger_workflow_id=str(endpoint['trigger_workflow_id']) if endpoint.get('trigger_workflow_id') else None,
            trigger_workflow_name=endpoint.get('trigger_workflow_name')
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid endpoint ID format"
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to regenerate API key: {str(e)}"
        )

@router.post("/{endpoint_id}/test", response_model=Dict[str, Any])
async def test_endpoint(
    endpoint_id: str,
    test_message: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Send a test message to the vendor endpoint. Creates an HL7 message and optionally test-triggers the linked workflow."""
    try:
        endpoint_uuid = uuid.UUID(endpoint_id)

        # First check if endpoint exists and belongs to tenant
        existing_endpoint = await VendorEndpointRepository.get_endpoint_by_id(endpoint_uuid)
        if not existing_endpoint:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )

        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)

        if existing_endpoint['tenant_id'] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vendor endpoint not found"
            )

        # Extract raw HL7 content from payload
        raw = test_message.get('message') if isinstance(test_message, dict) else None
        if not raw or not str(raw).strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty test message")

        raw_str = str(raw)

        # Parse HL7 (best-effort)
        parsed = None
        try:
            p = hl7_parser.parse_message(raw_str)
            parsed = {
                'message_type': p.message_type,
                'message_control_id': p.message_control_id,
                'event_type': p.event_type,
                'hl7_version': p.hl7_version,
                'sending_application': p.sending_application,
                'sending_facility': p.sending_facility,
                'receiving_application': p.receiving_application,
                'receiving_facility': p.receiving_facility,
                'encoding_characters': p.encoding_chars,
                'field_separator': p.field_separator,
                'parsed_dict': p.to_dict(),
            }
        except Exception:
            parsed = None

        # Determine ignore status before storing
        msg_type = (parsed or {}).get('message_type')
        ignore_list = [
            (t or '').strip().upper() for t in _normalize_list_field(existing_endpoint.get('ignored_message_types'))
        ]
        is_ignored = bool(msg_type) and str(msg_type).upper() in set(ignore_list)

        # Store message
        msg = await HL7MessageRepository.create_message(
            tenant_id=existing_endpoint['tenant_id'],
            raw_message=raw_str,
            message_type=(parsed or {}).get('message_type') or 'UNKNOWN',
            vendor_endpoint_id=existing_endpoint['id'],
            source_endpoint=f"{existing_endpoint['vendor_name']} ({existing_endpoint['vendor_slug']}) - TEST",
            message_direction='INBOUND',
            message_control_id=(parsed or {}).get('message_control_id'),
            event_type=(parsed or {}).get('event_type'),
            hl7_version=(parsed or {}).get('hl7_version'),
            sending_application=(parsed or {}).get('sending_application'),
            sending_facility=(parsed or {}).get('sending_facility'),
            receiving_application=(parsed or {}).get('receiving_application'),
            receiving_facility=(parsed or {}).get('receiving_facility'),
            # Ensure JSON-serializable string for DB compatibility across environments
            parsed_message=json.dumps((parsed or {}).get('parsed_dict')) if (parsed or {}).get('parsed_dict') is not None else None,
            status='IGNORED' if is_ignored else 'RECEIVED'
        )

        # Optionally trigger workflow in test mode
        # Determine if message type should be ignored for workflow execution
        # is_ignored already computed above

        workflow_triggered = bool(existing_endpoint.get('trigger_workflow_id')) and not is_ignored
        execution_id = None
        if workflow_triggered:
            try:
                result = await workflow_execution_service.execute_workflow(
                    workflow_id=str(existing_endpoint['trigger_workflow_id']),
                    trigger_data={
                        'message': raw_str,
                        'vendor_slug': existing_endpoint['vendor_slug'],
                        'vendor_name': existing_endpoint['vendor_name'],
                        'message_id': str(msg['id']),
                        'received_at': msg['created_at'].isoformat(),
                        'test_mode': True,
                    },
                    tenant_id=str(existing_endpoint['tenant_id']),
                    user_id=None,
                )
                execution_id = result.get('execution_id')
                if result.get('status') == 'COMPLETED':
                    await HL7MessageRepository.update_message_status(str(msg['id']), 'PROCESSED')
                else:
                    await HL7MessageRepository.update_message_status(str(msg['id']), 'FAILED', error_message=result.get('error'))
            except Exception as e:
                await HL7MessageRepository.update_message_status(str(msg['id']), 'FAILED', error_message=str(e))

        return {
            "message": "Test message ignored by filter" if is_ignored else "Test message processed",
            "endpoint_id": endpoint_id,
            "message_id": str(msg['id']),
            "workflow_triggered": workflow_triggered,
            "status": "IGNORED" if is_ignored else "TEST_COMPLETED",
            "ignored_by_filter": is_ignored,
            "execution_id": execution_id,
            "timestamp": datetime.now().isoformat(),
        }
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid endpoint ID format"
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send test message: {str(e)}"
        )
