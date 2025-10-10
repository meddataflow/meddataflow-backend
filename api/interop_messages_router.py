"""
Unified Multi-Format Healthcare Messages Router
Supports HL7, FHIR, DICOM, X12, NCPDP, CDA, CCD, CCR and other formats
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import base64
import json

from models.interop_message import (
    InteropMessageRepository,
    MessageStatus,
    MessageFormat,
    MessageDirection
)
from api.auth_deps import get_current_user, get_current_tenant
from processors.interoperability_processors_new import (
    process_fhir_parser_activity,
    process_fhir_translator_activity,
    process_dicom_parser_activity,
    process_dicom_translator_activity,
    process_x12_parser_activity,
    process_x12_translator_activity,
    process_ncpdp_parser_activity,
    process_ncpdp_translator_activity,
    process_cda_parser_activity,
    process_cda_translator_activity,
    process_ccd_parser_activity,
    process_ccd_translator_activity,
    process_ccr_parser_activity,
    process_ccr_translator_activity
)
from models.workflow_models import WorkflowContext

router = APIRouter(prefix="/api/interop/messages", tags=["interop-messages"])

# Pydantic models
class MessageResponse(BaseModel):
    id: str
    message_format: str
    message_control_id: Optional[str] = None
    message_type: str
    event_type: Optional[str] = None
    status: str
    direction: str
    raw_message_preview: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class MessageDetailResponse(BaseModel):
    id: str
    message_format: str
    message_control_id: Optional[str] = None
    message_type: str
    event_type: Optional[str] = None
    status: str
    direction: str
    raw_message: Optional[str] = None
    binary_payload: Optional[str] = None  # base64 encoded
    parsed_message: Optional[Dict[str, Any]] = None
    english_translation: Optional[Dict[str, Any]] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    workflow_name: Optional[str] = None
    vendor_name: Optional[str] = None

class MessageCreate(BaseModel):
    message_format: str
    raw_message: Optional[str] = None
    message_type: Optional[str] = None
    source_endpoint: Optional[str] = None

class MessageStats(BaseModel):
    total_messages: int
    received: int
    processing: int
    processed: int
    failed: int
    today: int
    unique_formats: int
    unique_message_types: int
    hl7_count: int
    fhir_count: int
    dicom_count: int
    x12_count: int
    ncpdp_count: int

class ParseRequest(BaseModel):
    message_format: str
    payload: str  # Can be text or base64 for binary
    parse_config: Optional[Dict[str, Any]] = None

class ParseResponse(BaseModel):
    success: bool
    message_format: str
    parsed_data: Optional[Dict[str, Any]] = None
    english_translation: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class TranslateRequest(BaseModel):
    message_format: str
    payload: Any
    target_format: Optional[str] = "english"
    config: Optional[Dict[str, Any]] = None

class TranslateResponse(BaseModel):
    success: bool
    source_format: str
    target_format: str
    translated_data: Optional[Any] = None
    error: Optional[str] = None

@router.get("/", response_model=List[MessageResponse])
async def get_messages(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    message_format: Optional[str] = None,
    message_status: Optional[str] = None,
    message_type: Optional[str] = None,
    exclude_format: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get healthcare messages for current tenant (all formats)"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)

        status_enum = MessageStatus[message_status.upper()] if message_status else None
        requested_format = message_format.lower() if message_format else None
        excluded_format = exclude_format.lower() if exclude_format and not requested_format else None

        messages = await InteropMessageRepository.get_messages_by_tenant(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            status=status_enum,
            message_format=requested_format,
            message_type=message_type,
            exclude_format=excluded_format
        )

        return [
            MessageResponse(
                id=str(msg['id']),
                message_format=msg.get('message_format', 'hl7'),
                message_control_id=msg.get('message_control_id'),
                message_type=msg.get('message_type', 'UNKNOWN'),
                event_type=msg.get('event_type'),
                status=msg['status'],
                direction=msg['direction'],
                raw_message_preview=msg.get('raw_message_preview'),
                file_name=msg.get('file_name'),
                file_size=msg.get('file_size'),
                mime_type=msg.get('mime_type'),
                created_at=msg['created_at'],
                updated_at=msg['updated_at']
            )
            for msg in messages
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch messages: {str(e)}"
        )

@router.get("/{message_id}/dicom-file")
async def get_dicom_file(
    message_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get raw DICOM binary file for cornerstone viewer"""
    try:
        message_uuid = uuid.UUID(message_id)

        # Get binary payload directly from database
        from database.connection import fetch_one
        query = """
        SELECT binary_payload, message_format, file_name, tenant_id
        FROM hl7_messages
        WHERE id = $1
        """
        result = await fetch_one(query, message_uuid)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found"
            )

        # Verify tenant ownership
        if str(result['tenant_id']) != str(current_tenant['id']):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )

        # Verify it's a DICOM file
        if result.get('message_format') != 'dicom':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Message is not a DICOM file"
            )

        binary_payload = result.get('binary_payload')
        if not binary_payload:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="DICOM file data not found"
            )

        # Return raw binary data with proper DICOM content type
        return Response(
            content=bytes(binary_payload),
            media_type="application/dicom",
            headers={
                "Content-Disposition": f'inline; filename="{result.get("file_name", "image.dcm")}"'
            }
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid message ID format"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch DICOM file: {str(e)}"
        )

@router.get("/{message_id}", response_model=MessageDetailResponse)
async def get_message(
    message_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get detailed message by ID"""
    try:
        message_uuid = uuid.UUID(message_id)
        msg = await InteropMessageRepository.get_message_by_id(message_uuid)

        if not msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found"
            )

        # Verify tenant ownership
        if str(msg['tenant_id']) != str(current_tenant['id']):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )

        # PHI masking: only admins see full raw_message
        user_role = (current_user.get('role') or '').upper()
        can_view_phi = user_role in ('SUPER_ADMIN', 'TENANT_ADMIN', 'WORKFLOW_ADMIN')

        return MessageDetailResponse(
            id=str(msg['id']),
            message_format=msg.get('message_format', 'hl7'),
            message_control_id=msg.get('message_control_id'),
            message_type=msg.get('message_type', 'UNKNOWN'),
            event_type=msg.get('event_type'),
            status=msg['status'],
            direction=msg['direction'],
            raw_message=msg.get('raw_message') if can_view_phi else '[REDACTED]',
            binary_payload=msg.get('binary_payload') if can_view_phi else None,
            parsed_message=msg.get('parsed_message'),
            english_translation=msg.get('english_translation'),
            file_name=msg.get('file_name'),
            file_size=msg.get('file_size'),
            mime_type=msg.get('mime_type'),
            created_at=msg['created_at'],
            updated_at=msg['updated_at'],
            workflow_name=msg.get('workflow_name'),
            vendor_name=msg.get('vendor_name')
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid message ID format"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch message: {str(e)}"
        )

@router.post("/", response_model=MessageResponse)
async def create_message(
    message: MessageCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Create a new healthcare message"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)

        user_id = current_user['id']
        if isinstance(user_id, str):
            user_id = uuid.UUID(user_id)

        created_msg = await InteropMessageRepository.create_message(
            tenant_id=tenant_id,
            message_format=message.message_format,
            raw_message=message.raw_message,
            message_type=message.message_type,
            created_by_id=user_id,
            source_endpoint=message.source_endpoint,
            message_direction=MessageDirection.INBOUND.value
        )

        return MessageResponse(
            id=str(created_msg['id']),
            message_format=created_msg.get('message_format', 'hl7'),
            message_control_id=created_msg.get('message_control_id'),
            message_type=created_msg.get('message_type', 'UNKNOWN'),
            event_type=created_msg.get('event_type'),
            status=created_msg['status'],
            direction=created_msg['direction'],
            raw_message_preview=created_msg.get('raw_message')[:500] if created_msg.get('raw_message') else None,
            file_name=created_msg.get('file_name'),
            file_size=created_msg.get('file_size'),
            mime_type=created_msg.get('mime_type'),
            created_at=created_msg['created_at'],
            updated_at=created_msg['updated_at']
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create message: {str(e)}"
        )

@router.post("/upload", response_model=MessageResponse)
async def upload_message(
    file: UploadFile = File(...),
    message_format: str = Form(...),
    message_type: Optional[str] = Form(None),
    source_endpoint: Optional[str] = Form(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Upload a healthcare message file (supports binary formats like DICOM)"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)

        user_id = current_user['id']
        if isinstance(user_id, str):
            user_id = uuid.UUID(user_id)

        # Read file content
        file_content = await file.read()
        file_size = len(file_content)

        # Determine if binary or text
        is_binary = message_format.lower() in ['dicom', 'custom']
        raw_message = None
        binary_payload = None

        if is_binary:
            binary_payload = file_content
        else:
            try:
                raw_message = file_content.decode('utf-8')
            except UnicodeDecodeError:
                # Treat as binary if decode fails
                binary_payload = file_content

        created_msg = await InteropMessageRepository.create_message(
            tenant_id=tenant_id,
            message_format=message_format,
            raw_message=raw_message,
            binary_payload=binary_payload,
            message_type=message_type or 'FILE_UPLOAD',
            created_by_id=user_id,
            source_endpoint=source_endpoint,
            message_direction=MessageDirection.INBOUND.value,
            file_name=file.filename,
            mime_type=file.content_type
        )

        return MessageResponse(
            id=str(created_msg['id']),
            message_format=created_msg.get('message_format', 'hl7'),
            message_control_id=created_msg.get('message_control_id'),
            message_type=created_msg.get('message_type', 'UNKNOWN'),
            event_type=created_msg.get('event_type'),
            status=created_msg['status'],
            direction=created_msg['direction'],
            raw_message_preview='[BINARY FILE]' if binary_payload else (raw_message[:500] if raw_message else None),
            file_name=created_msg.get('file_name'),
            file_size=created_msg.get('file_size'),
            mime_type=created_msg.get('mime_type'),
            created_at=created_msg['created_at'],
            updated_at=created_msg['updated_at']
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload message: {str(e)}"
        )

@router.delete("/{message_id}")
async def delete_message(
    message_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Delete a message"""
    try:
        message_uuid = uuid.UUID(message_id)

        # Verify ownership before delete
        msg = await InteropMessageRepository.get_message_by_id(message_uuid)
        if not msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found"
            )

        if str(msg['tenant_id']) != str(current_tenant['id']):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )

        await InteropMessageRepository.delete_message(message_uuid)
        return {"message": "Message deleted successfully"}
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid message ID format"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete message: {str(e)}"
        )

@router.get("/stats/summary", response_model=MessageStats)
async def get_message_stats(
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get message statistics across all formats"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)

        stats = await InteropMessageRepository.get_message_stats(tenant_id)

        return MessageStats(
            total_messages=int(stats.get('total_messages', 0)),
            received=int(stats.get('received', 0)),
            processing=int(stats.get('processing', 0)),
            processed=int(stats.get('processed', 0)),
            failed=int(stats.get('failed', 0)),
            today=int(stats.get('today', 0)),
            unique_formats=int(stats.get('unique_formats', 0)),
            unique_message_types=int(stats.get('unique_message_types', 0)),
            hl7_count=int(stats.get('hl7_count', 0)),
            fhir_count=int(stats.get('fhir_count', 0)),
            dicom_count=int(stats.get('dicom_count', 0)),
            x12_count=int(stats.get('x12_count', 0)),
            ncpdp_count=int(stats.get('ncpdp_count', 0))
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch stats: {str(e)}"
        )

@router.post("/parse", response_model=ParseResponse)
async def parse_message(
    request: ParseRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Parse a healthcare message and extract structured data"""
    try:
        message_format = request.message_format.lower()

        # Create a minimal workflow context
        context = WorkflowContext(
            execution_id="parse-" + str(uuid.uuid4()),
            workflow_id=uuid.uuid4(),
            tenant_id=uuid.UUID(current_tenant['id']) if isinstance(current_tenant['id'], str) else current_tenant['id'],
            raw_message=request.payload,
            variables={}
        )

        # Select parser based on format
        parser_map = {
            'fhir': process_fhir_parser_activity,
            'dicom': process_dicom_parser_activity,
            'x12': process_x12_parser_activity,
            'ncpdp': process_ncpdp_parser_activity,
            'cda': process_cda_parser_activity,
            'ccd': process_ccd_parser_activity,
            'ccr': process_ccr_parser_activity
        }

        parser_func = parser_map.get(message_format)
        if not parser_func:
            return ParseResponse(
                success=False,
                message_format=message_format,
                error=f"Parser not available for format: {message_format}"
            )

        activity_config = {
            'config': request.parse_config or {}
        }

        result = await parser_func(activity_config, context)

        if result.status.value.upper() == "COMPLETED":
            return ParseResponse(
                success=True,
                message_format=message_format,
                parsed_data=result.output_data,
                english_translation=result.variables.get('english_translation')
            )
        else:
            return ParseResponse(
                success=False,
                message_format=message_format,
                error=result.error_message
            )
    except Exception as e:
        return ParseResponse(
            success=False,
            message_format=request.message_format,
            error=str(e)
        )

@router.post("/translate", response_model=TranslateResponse)
async def translate_message(
    request: TranslateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Translate a healthcare message to human-readable English or another format"""
    try:
        message_format = request.message_format.lower()

        # Create a minimal workflow context
        base_variables: Dict[str, Any] = {f"{message_format}_resource": request.payload}

        if isinstance(request.payload, dict):
            if message_format in {"cda", "ccd", "ccr"}:
                base_variables[f"{message_format}_document"] = request.payload
                summary = request.payload.get("summary")
                if summary:
                    base_variables[f"{message_format}_summary"] = summary
            elif message_format == "ncpdp":
                base_variables.setdefault("ncpdp_message", request.payload)

        raw_message = (
            json.dumps(request.payload)
            if isinstance(request.payload, dict)
            else str(request.payload)
        )

        context = WorkflowContext(
            execution_id="translate-" + str(uuid.uuid4()),
            workflow_id=uuid.uuid4(),
            tenant_id=uuid.UUID(current_tenant['id']) if isinstance(current_tenant['id'], str) else current_tenant['id'],
            raw_message=raw_message,
            variables=base_variables
        )

        # Select translator based on format
        translator_map = {
            'fhir': process_fhir_translator_activity,
            'dicom': process_dicom_translator_activity,
            'x12': process_x12_translator_activity,
            'ncpdp': process_ncpdp_translator_activity,
            'cda': process_cda_translator_activity,
            'ccd': process_ccd_translator_activity,
            'ccr': process_ccr_translator_activity
        }

        translator_func = translator_map.get(message_format)
        if not translator_func:
            return TranslateResponse(
                success=False,
                source_format=message_format,
                target_format=request.target_format or "english",
                error=f"Translator not available for format: {message_format}"
            )

        activity_config = {
            'config': {
                'target_format': request.target_format or 'english',
                **(request.config or {})
            }
        }

        result = await translator_func(activity_config, context)

        if result.status.value.upper() == "COMPLETED":
            return TranslateResponse(
                success=True,
                source_format=message_format,
                target_format=request.target_format or "english",
                translated_data=result.output_data
            )
        else:
            return TranslateResponse(
                success=False,
                source_format=message_format,
                target_format=request.target_format or "english",
                error=result.error_message
            )
    except Exception as e:
        return TranslateResponse(
            success=False,
            source_format=request.message_format,
            target_format=request.target_format or "english",
            error=str(e)
        )
