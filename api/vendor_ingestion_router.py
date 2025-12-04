from fastapi import APIRouter, HTTPException, Depends, Request, BackgroundTasks, UploadFile, File, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import uuid
import json

from database.connection import fetch_one, execute, fetch_all
from services.hl7_parser import HL7Parser
from services.queue_service import queue_service
from models.tenant import TenantRepository


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vendor", tags=["Vendor Message Ingestion"])

# Security scheme for API key
security = HTTPBearer()

# Global instances
hl7_parser = HL7Parser()

SUPPORTED_MESSAGE_FORMATS = {
    "hl7",
    "fhir",
    "dicom",
    "ncpdp",
    "x12",
    "cda",
    "ccd",
    "ccr",
    "terminology",
    "csv",
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
    "xml": "cda",
    "csv": "csv",
}


def normalize_ingestion_format(value: Optional[str]) -> str:
    raw = (value or "hl7").strip().lower()
    normalized = MESSAGE_FORMAT_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_MESSAGE_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported message format '{value}'. Supported: {', '.join(sorted(SUPPORTED_MESSAGE_FORMATS))}"
        )
    return normalized

async def validate_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """
    Validate vendor API key and return vendor endpoint information
    """
    api_key = credentials.credentials
    
    if not api_key:
        raise HTTPException(status_code=401, detail="API key is required")
    
    # Look up vendor endpoint by API key
    query = """
    SELECT ve.*, t.name as tenant_name, t.slug as tenant_slug
    FROM vendor_endpoints ve
    JOIN tenants t ON ve.tenant_id = t.id
    WHERE ve.api_key = $1 AND ve.is_active = true AND t.is_active = true
    """
    
    vendor_endpoint = await fetch_one(query, api_key)
    
    if not vendor_endpoint:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    try:
        normalized_format = normalize_ingestion_format(vendor_endpoint.get('message_format'))
    except HTTPException:
        normalized_format = "hl7"

    return {
        "vendor_endpoint_id": vendor_endpoint['id'],
        "vendor_slug": vendor_endpoint['vendor_slug'],
        "vendor_name": vendor_endpoint['vendor_name'],
        "tenant_id": vendor_endpoint['tenant_id'],
        "tenant_name": vendor_endpoint['tenant_name'],
        "tenant_slug": vendor_endpoint['tenant_slug'],
        "trigger_workflow_id": vendor_endpoint['trigger_workflow_id'],
        "message_format": normalized_format,
        "max_message_size": vendor_endpoint['max_message_size'],
        "rate_limit_per_hour": vendor_endpoint['rate_limit_per_hour'],
        "ack_on_receive": vendor_endpoint.get('ack_on_receive', False),
        "ack_profile": vendor_endpoint.get('ack_profile')
    }

async def check_rate_limit(vendor_info: Dict[str, Any], request: Request) -> bool:
    """
    Check if the vendor has exceeded their rate limit
    """
    # Simple rate limiting - count messages in the last hour
    from datetime import datetime, timedelta
    
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    
    query = """
    SELECT COUNT(*) as message_count
    FROM hl7_messages
    WHERE vendor_endpoint_id = $1 AND created_at > $2
    """
    
    result = await fetch_one(query, vendor_info["vendor_endpoint_id"], one_hour_ago)
    message_count = result['message_count'] if result else 0
    
    rate_limit = vendor_info['rate_limit_per_hour']
    
    if message_count >= rate_limit:
        logger.warning(f"Rate limit exceeded for vendor {vendor_info['vendor_slug']}: {message_count}/{rate_limit}")
        return False
    
    return True

async def _ingest_vendor_message_internal(
    vendor_slug: str,
    format_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    raw_message: Optional[str],
    file: Optional[UploadFile],
    vendor_info: Dict[str, Any]
):
    """
    Secure HL7 message ingestion endpoint for vendors

    This endpoint:
    1. Validates the vendor's API key
    2. Checks rate limits
    3. Validates message size
    4. Parses and stores the HL7 message
    5. Triggers the associated workflow (if configured)
    6. Returns processing status
    """

    # Input validation and security checks
    if (not raw_message or not isinstance(raw_message, str)) and file:
        try:
            content_bytes = await file.read()
            raw_message = content_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {exc}")
    if not raw_message or not isinstance(raw_message, str):
        raise HTTPException(status_code=400, detail="Invalid message format")

    # Check message size limits
    message_size = len(raw_message.encode('utf-8'))
    max_size = vendor_info.get('max_message_size', 1024 * 1024)  # 1MB default
    if message_size > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"Message too large: {message_size} bytes (max: {max_size} bytes)"
        )

    # Check for suspicious content (applies to all formats)
    if any(suspicious in raw_message.lower() for suspicious in ['<script', 'javascript:', 'eval(', 'exec(']):
        logger.warning(f"Suspicious content detected in message from {vendor_info['vendor_slug']}")
        raise HTTPException(status_code=400, detail="Message contains suspicious content")
    
    try:
        # Verify vendor slug matches the API key
        if vendor_slug != vendor_info['vendor_slug']:
            raise HTTPException(status_code=403, detail="Vendor slug does not match API key")

        path_message_format = normalize_ingestion_format(format_slug)
        vendor_configured_format = normalize_ingestion_format(vendor_info.get('message_format'))

        if path_message_format != vendor_configured_format:
            raise HTTPException(
                status_code=400,
                detail=f"Vendor expects {vendor_configured_format.upper()} messages, but endpoint path requested {path_message_format.upper()}"
            )

        message_format = vendor_configured_format
        vendor_info = {**vendor_info, 'message_format': message_format}

        # Check rate limiting
        if not await check_rate_limit(vendor_info, request):
            raise HTTPException(
                status_code=429, 
                detail=f"Rate limit exceeded. Maximum {vendor_info['rate_limit_per_hour']} messages per hour."
            )

        # Plan/subscription enforcement
        tenant = await TenantRepository.get_tenant_by_id(vendor_info['tenant_id'])
        if not isinstance(tenant, dict):
            tenant = {}
        plan = (tenant.get('plan') or 'PROFESSIONAL').upper()
        from datetime import timezone
        start_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Usage this month
        monthly_usage = await fetch_one("SELECT COUNT(*) AS c FROM hl7_messages WHERE tenant_id = $1 AND created_at >= $2", vendor_info['tenant_id'], start_month)
        usage_count = monthly_usage['c'] if monthly_usage else 0
        plan_included = {'FREE': 1000, 'PROFESSIONAL': 100000, 'ENTERPRISE': 2000000}.get(plan, 100000)
        billing_settings = ((tenant.get('settings') or {}).get('billing') or {}) if isinstance(tenant.get('settings'), dict) else {}
        billing_exempt = bool(billing_settings.get('billing_exempt', False))
        # If no subscription status is present, treat as active to avoid blocking ingestion in non-billed environments
        subscription_status = (billing_settings.get('subscription_status') or 'active').lower()
        # Require subscription for paid plans unless exempt or trialing
        if plan != 'FREE' and not billing_exempt and subscription_status not in ('active', 'trialing'):
            raise HTTPException(status_code=402, detail="Subscription required for this tenant. Please subscribe or contact support.")
        enforce_cap = bool(billing_settings.get('enforce_cap', False))
        if enforce_cap and usage_count >= plan_included:
            raise HTTPException(status_code=402, detail="Plan limit exceeded. Please upgrade or disable enforce_cap to continue.")
        
        # Validate message size
        message_size = len(raw_message.encode('utf-8'))
        max_size = vendor_info['max_message_size']
        
        if message_size > max_size:
            raise HTTPException(
                status_code=413, 
                detail=f"Message size {message_size} bytes exceeds limit of {max_size} bytes"
            )

        insert_query = """
        INSERT INTO hl7_messages (
            id, tenant_id, vendor_endpoint_id, message_control_id,
            message_type, event_type, hl7_version, raw_message, parsed_message,
            sending_application, sending_facility, receiving_application, receiving_facility,
            status, direction, validation_errors, source_endpoint, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
        RETURNING *
        """

        if message_format != 'hl7':
            return await _process_non_hl7_message(
                insert_query,
                vendor_slug,
                raw_message,
                vendor_info,
                message_format
            )

        # HL7-specific validation
        if not raw_message.startswith('MSH|'):
            raise HTTPException(status_code=400, detail="Invalid HL7 message format")

        try:
            parsed_message = hl7_parser.parse_message(raw_message)
            validation_errors = hl7_parser.validate_message(parsed_message)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid HL7 message: {str(e)}")

        # Generate message ID
        message_id = uuid.uuid4()

        # Extract key fields from parsed message
        message_control_id = parsed_message.message_control_id
        message_type = parsed_message.message_type
        event_type = getattr(parsed_message, 'event_type', None)
        hl7_version = getattr(parsed_message, 'version', '2.5')
        sending_application = getattr(parsed_message, 'sending_application', '')
        sending_facility = getattr(parsed_message, 'sending_facility', '')
        receiving_application = getattr(parsed_message, 'receiving_application', '')
        receiving_facility = getattr(parsed_message, 'receiving_facility', '')

        # Convert parsed message to JSON for storage
        parsed_message_json = {
            "message_type": message_type,
            "message_control_id": message_control_id,
            "version": hl7_version,
            "segments": []
        }

        for segment in parsed_message.segments:
            segment_data = {
                "type": segment.type,
                "sequence": segment.sequence,
                "fields": []
            }
            for i, field in enumerate(segment.fields, 1):
                field_data = {
                    "sequence": i,
                    "name": getattr(field, 'description', f"Field {i}"),
                    "value": field.value,
                    "path": field.path
                }
                segment_data["fields"].append(field_data)
            parsed_message_json["segments"].append(segment_data)

        # Insert message
        stored_message = await execute(
            insert_query,
            message_id,
            vendor_info['tenant_id'],
            vendor_info['vendor_endpoint_id'],
            message_control_id,
            message_type,
            event_type,
            hl7_version,
            raw_message,
            json.dumps(parsed_message_json),
            sending_application,
            sending_facility,
            receiving_application,
            receiving_facility,
            'RECEIVED',
            'INBOUND',
            json.dumps(validation_errors) if validation_errors else None,
            f"vendor:{vendor_slug}",
            datetime.utcnow()
        )
        
        # Update vendor endpoint statistics
        stats_update_query = """
        UPDATE vendor_endpoints 
        SET total_messages_received = total_messages_received + 1,
            updated_at = $2
        WHERE id = $1
        """
        await execute(stats_update_query, vendor_info['vendor_endpoint_id'], datetime.utcnow())

        # Prepare response
        response_data = {
            "status": "success",
            "message": "HL7 message received and processed",
            "message_id": str(message_id),
            "message_control_id": message_control_id,
            "message_type": message_type,
            "validation_errors": validation_errors,
            "is_valid": len(validation_errors) == 0,
            "received_at": datetime.utcnow().isoformat(),
            "vendor_info": {
                "vendor_slug": vendor_info['vendor_slug'],
                "vendor_name": vendor_info['vendor_name'],
                "tenant_slug": vendor_info['tenant_slug'],
                "message_format": vendor_info['message_format']
            }
        }

        # Optionally return HL7 ACK directly for clients that expect it
        try:
            if bool(vendor_info.get('ack_on_receive')):
                from fastapi.responses import PlainTextResponse
                from services.hl7_ack import generate_ack
                code = 'AA' if not validation_errors else 'AE'
                ack_text = generate_ack(raw_message, code=code, error_text=(validation_errors[0] if validation_errors else None), profile=str(vendor_info.get('ack_profile') or 'default'))
                # If client prefers text/plain via Accept header or query param format=hl7, return ACK text
                accept = request.headers.get('accept', '')
                fmt = request.query_params.get('format')
                if 'text/plain' in accept or (fmt and fmt.lower() in ('hl7', 'ack')):
                    return PlainTextResponse(content=ack_text, media_type='text/plain')
                else:
                    response_data['ack'] = ack_text
        except Exception:
            pass
        
        # Trigger workflow if configured (using queue service)
        if vendor_info['trigger_workflow_id']:
            try:
                task_id = await queue_service.enqueue_workflow_execution(
                    workflow_id=vendor_info['trigger_workflow_id'],
                    message_id=str(message_id),
                    raw_message=raw_message,
                    vendor_info=vendor_info,
                    priority=3  # High priority for HL7 message processing
                )
                response_data["workflow_triggered"] = True
                response_data["workflow_id"] = vendor_info['trigger_workflow_id']
                response_data["task_id"] = task_id
            except Exception as e:
                logger.error(f"Failed to enqueue workflow execution: {e}")
                response_data["workflow_triggered"] = False
                response_data["workflow_error"] = str(e)
        else:
            response_data["workflow_triggered"] = False
            response_data["note"] = "No workflow configured for this vendor endpoint"
        
        return response_data
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing HL7 message from vendor {vendor_slug}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error processing message")


async def _process_non_hl7_message(
    insert_query: str,
    vendor_slug: str,
    raw_message: str,
    vendor_info: Dict[str, Any],
    message_format: str
):
    """Handle ingestion for non-HL7 message formats."""

    message_id = uuid.uuid4()
    message_control_id = str(uuid.uuid4())
    message_type = message_format.upper()
    validation_errors: List[str] = []

    metadata: Dict[str, Any] = {
        "format": message_format,
        "ingested_at": datetime.utcnow().isoformat()
    }

    try:
        if message_format == 'fhir':
            resource = json.loads(raw_message)
            resource_type = resource.get('resourceType', 'Resource')
            message_type = f"FHIR:{resource_type}"
            message_control_id = resource.get('id', message_control_id)
            metadata.update({
                "resourceType": resource_type,
                "id": resource.get('id'),
                "meta": resource.get('meta')
            })
        elif message_format == 'x12':
            metadata.update({
                "segment_count": raw_message.count('~') + 1 if '~' in raw_message else 1
            })
        elif message_format == 'ncpdp':
            metadata.update({
                "field_delimiter": '|'
            })
        elif message_format in {'cda', 'ccd', 'ccr'}:
            metadata.update({
                "document_hint": message_format.upper()
            })
        elif message_format == 'terminology':
            try:
                sample = json.loads(raw_message)
                metadata.update({
                    "code": sample.get('code'),
                    "system": sample.get('system'),
                    "display": sample.get('display')
                })
            except json.JSONDecodeError:
                metadata.update({
                    "note": "Terminology payload stored as raw text"
                })
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {message_format.upper()} payload: {str(exc)}")

    metadata["byte_size"] = len(raw_message.encode('utf-8'))

    await execute(
        insert_query,
        message_id,
        vendor_info['tenant_id'],
        vendor_info['vendor_endpoint_id'],
        message_control_id,
        message_type,
        None,
        None,
        raw_message,
        json.dumps({"format": message_format, "metadata": metadata}),
        vendor_info.get('vendor_name', ''),
        '',
        vendor_info.get('tenant_slug', ''),
        '',
        'RECEIVED',
        'INBOUND',
        None,
        f"vendor:{vendor_slug}",
        datetime.utcnow()
    )

    stats_update_query = """
    UPDATE vendor_endpoints 
    SET total_messages_received = total_messages_received + 1,
        updated_at = $2
    WHERE id = $1
    """
    await execute(stats_update_query, vendor_info['vendor_endpoint_id'], datetime.utcnow())

    response_data = {
        "status": "success",
        "message": f"{message_format.upper()} message received",
        "message_id": str(message_id),
        "message_control_id": message_control_id,
        "message_type": message_type,
        "validation_errors": validation_errors,
        "is_valid": len(validation_errors) == 0,
        "received_at": datetime.utcnow().isoformat(),
        "vendor_info": {
            "vendor_slug": vendor_info['vendor_slug'],
            "vendor_name": vendor_info['vendor_name'],
            "tenant_slug": vendor_info['tenant_slug'],
            "message_format": vendor_info['message_format']
        }
    }

    if vendor_info['trigger_workflow_id']:
        try:
            task_id = await queue_service.enqueue_workflow_execution(
                workflow_id=vendor_info['trigger_workflow_id'],
                message_id=str(message_id),
                raw_message=raw_message,
                vendor_info=vendor_info,
                priority=3
            )
            response_data["workflow_triggered"] = True
            response_data["workflow_id"] = vendor_info['trigger_workflow_id']
            response_data["task_id"] = task_id
        except Exception as exc:
            logger.error(f"Failed to enqueue workflow execution for non-HL7 message: {exc}")
            response_data["workflow_triggered"] = False
            response_data["workflow_error"] = str(exc)
    else:
        response_data["workflow_triggered"] = False
        response_data["note"] = "No workflow configured for this vendor endpoint"

    return response_data


@router.post("/{vendor_slug}/{format_slug}/ingest")
async def ingest_vendor_message(
    vendor_slug: str,
    format_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    raw_message: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    vendor_info: Dict[str, Any] = Depends(validate_api_key)
):
    """Generic ingestion endpoint for any supported format (uses path format slug)."""
    return await _ingest_vendor_message_internal(
        vendor_slug, format_slug, request, background_tasks, raw_message, file, vendor_info
    )

async def trigger_workflow_async(
    workflow_id: str,
    message_id: str,
    raw_message: str,
    parsed_message,
    vendor_info: Dict[str, Any]
):
    """
    Background task to trigger workflow execution
    """
    try:
        # Get workflow from database
        workflow_query = "SELECT * FROM workflows WHERE id = $1 AND is_active = true"
        workflow = await fetch_one(workflow_query, workflow_id)
        
        if not workflow:
            logger.error(f"Workflow {workflow_id} not found or inactive")
            return
        
        # Update message status
        await execute(
            "UPDATE hl7_messages SET status = 'PROCESSING' WHERE id = $1",
            message_id
        )
        
        # Prepare trigger data
        trigger_data = {
            "message_id": message_id,
            "raw_message": raw_message,
            "message_type": parsed_message.message_type,
            "message_control_id": parsed_message.message_control_id,
            "vendor_slug": vendor_info['vendor_slug'],
            "vendor_name": vendor_info['vendor_name'],
            "tenant_id": str(vendor_info['tenant_id']),
            "source": "vendor_ingestion"
        }
        
        # Execute workflow (this is a simplified version - in production you'd use a proper workflow object)
        execution_id = f"exec_{uuid.uuid4()}"
        
        
        # Update message status to processed
        await execute(
            "UPDATE hl7_messages SET status = 'PROCESSED', processed_at = $2 WHERE id = $1",
            message_id,
            datetime.utcnow()
        )
        
        # Update vendor endpoint statistics
        await execute(
            "UPDATE vendor_endpoints SET total_messages_processed = total_messages_processed + 1 WHERE id = $1",
            vendor_info['vendor_endpoint_id']
        )
        
    except Exception as e:
        logger.error(f"Error triggering workflow {workflow_id} for message {message_id}: {e}")
        
        # Update message status to failed
        await execute(
            "UPDATE hl7_messages SET status = 'FAILED', processing_errors = $2 WHERE id = $1",
            message_id,
            json.dumps({"error": str(e), "timestamp": datetime.utcnow().isoformat()})
        )
        
        # Update vendor endpoint statistics
        await execute(
            "UPDATE vendor_endpoints SET total_messages_failed = total_messages_failed + 1 WHERE id = $1",
            vendor_info['vendor_endpoint_id']
        )

@router.get("/{vendor_slug}/status")
async def get_vendor_status(
    vendor_slug: str,
    vendor_info: Dict[str, Any] = Depends(validate_api_key)
):
    """
    Get vendor endpoint status and statistics
    """
    
    if vendor_slug != vendor_info['vendor_slug']:
        raise HTTPException(status_code=403, detail="Vendor slug does not match API key")
    
    # Get recent message statistics
    stats_query = """
    SELECT 
        COUNT(*) as total_messages,
        COUNT(*) FILTER (WHERE status = 'PROCESSED') as processed_messages,
        COUNT(*) FILTER (WHERE status = 'FAILED') as failed_messages,
        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as messages_last_hour,
        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as messages_last_24h
    FROM hl7_messages 
    WHERE vendor_endpoint_id = $1
    """
    
    stats = await fetch_one(stats_query, vendor_info['vendor_endpoint_id'])
    
    return {
        "vendor_info": {
            "vendor_slug": vendor_info['vendor_slug'],
            "vendor_name": vendor_info['vendor_name'],
            "tenant_name": vendor_info['tenant_name'],
            "is_active": True
        },
        "configuration": {
            "message_format": vendor_info['message_format'],
            "max_message_size": vendor_info['max_message_size'],
            "rate_limit_per_hour": vendor_info['rate_limit_per_hour'],
            "has_workflow": vendor_info['trigger_workflow_id'] is not None
        },
        "statistics": {
            "total_messages": stats['total_messages'] if stats else 0,
            "processed_messages": stats['processed_messages'] if stats else 0,
            "failed_messages": stats['failed_messages'] if stats else 0,
            "messages_last_hour": stats['messages_last_hour'] if stats else 0,
            "messages_last_24h": stats['messages_last_24h'] if stats else 0,
            "success_rate": round((stats['processed_messages'] / max(stats['total_messages'], 1)) * 100, 2) if stats and stats['total_messages'] > 0 else 0
        },
        "rate_limiting": {
            "current_hour_usage": stats['messages_last_hour'] if stats else 0,
            "hourly_limit": vendor_info['rate_limit_per_hour'],
            "remaining": max(0, vendor_info['rate_limit_per_hour'] - (stats['messages_last_hour'] if stats else 0))
        }
    }

@router.get("/{vendor_slug}/messages")
async def get_recent_messages(
    vendor_slug: str,
    limit: int = 10,
    offset: int = 0,
    status: Optional[str] = None,
    vendor_info: Dict[str, Any] = Depends(validate_api_key)
):
    """
    Get recent messages for this vendor endpoint
    """
    
    if vendor_slug != vendor_info['vendor_slug']:
        raise HTTPException(status_code=403, detail="Vendor slug does not match API key")
    
    # Build query
    base_query = """
    SELECT id, message_control_id, message_type, status, created_at, processed_at,
           validation_errors, source_endpoint
    FROM hl7_messages 
    WHERE vendor_endpoint_id = $1
    """
    
    params = [vendor_info['vendor_endpoint_id']]
    
    if status:
        base_query += " AND status = $2"
        params.append(status)
        
    base_query += " ORDER BY created_at DESC LIMIT $" + str(len(params) + 1) + " OFFSET $" + str(len(params) + 2)
    params.extend([limit, offset])
    
    messages = await fetch_all(base_query, *params)
    
    return {
        "messages": [
            {
                "message_id": str(msg['id']),
                "message_control_id": msg['message_control_id'],
                "message_type": msg['message_type'],
                "status": msg['status'],
                "created_at": msg['created_at'].isoformat() if msg['created_at'] else None,
                "processed_at": msg['processed_at'].isoformat() if msg['processed_at'] else None,
                "has_validation_errors": bool(msg['validation_errors']),
                "source_endpoint": msg['source_endpoint']
            }
            for msg in messages
        ],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": len(messages)
        }
    }
@router.post("/{vendor_slug}/hl7/ingest")
async def ingest_hl7_message_compat(
    vendor_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    raw_message: str,
    vendor_info: Dict[str, Any] = Depends(validate_api_key)
):
    return await ingest_vendor_message(
        vendor_slug=vendor_slug,
        format_slug="hl7",
        request=request,
        background_tasks=background_tasks,
        raw_message=raw_message,
        vendor_info=vendor_info
    )
