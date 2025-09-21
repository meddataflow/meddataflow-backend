"""
HL7 Message Ingestion Router - Handles incoming HL7 messages from EMRs
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid
import asyncio
import json

from models.vendor_endpoint import VendorEndpointRepository
from models.hl7_message import HL7MessageRepository
from services.workflow_execution_service import WorkflowExecutionService
from services.hl7_parser import HL7Parser
from database.connection import get_pool

router = APIRouter(prefix="/api/vendor", tags=["hl7-ingestion"])
security = HTTPBearer()

# Initialize HL7 parser
hl7_parser = HL7Parser()

class HL7IngestionResponse(BaseModel):
    message: str
    message_id: str
    status: str
    received_at: datetime
    workflow_triggered: bool = False
    execution_id: Optional[str] = None
    ignored_by_filter: Optional[bool] = False

def _normalize_list_field(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v is not None]
        except Exception:
            pass
        return [s.strip() for s in value.split(',') if s.strip()]
    return []

class MessageProcessingQueue:
    """Simple in-memory queue for high-volume message processing"""
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing = False

    async def add_message(self, message_data: Dict[str, Any]):
        """Add message to processing queue"""
        await self.queue.put(message_data)
        if not self.processing:
            asyncio.create_task(self.process_queue())

    async def process_queue(self):
        """Process messages from the queue"""
        self.processing = True
        try:
            while not self.queue.empty():
                try:
                    message_data = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                    await self.process_message(message_data)
                    self.queue.task_done()
                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    print(f"Error processing message: {str(e)}")
        finally:
            self.processing = False

    async def process_message(self, message_data: Dict[str, Any]):
        """Process individual message and trigger workflow"""
        try:
            vendor_endpoint = message_data['vendor_endpoint']
            hl7_message = message_data['hl7_message']
            message_record = message_data['message_record']

            # Trigger workflow if connected
            if vendor_endpoint.get('trigger_workflow_id'):
                workflow_service = WorkflowExecutionService()

                # Execute workflow with the HL7 message
                result = await workflow_service.execute_workflow(
                    workflow_id=str(vendor_endpoint['trigger_workflow_id']),
                    trigger_data={
                        'message': hl7_message,
                        'vendor_slug': vendor_endpoint['vendor_slug'],
                        'vendor_name': vendor_endpoint['vendor_name'],
                        'message_id': str(message_record['id']),
                        'received_at': message_record['created_at'].isoformat()
                    },
                    tenant_id=str(vendor_endpoint['tenant_id']),
                    user_id=None  # System triggered execution
                )

                # Update message status based on workflow execution
                if result.get('status') == 'COMPLETED':
                    await HL7MessageRepository.update_message_status(
                        str(message_record['id']),
                        'PROCESSED',
                        processed_at=datetime.now(timezone.utc)
                    )
                    await VendorEndpointRepository.increment_message_stats(
                        vendor_endpoint['id'],
                        received=False,
                        processed=True,
                        failed=False
                    )
                else:
                    await HL7MessageRepository.update_message_status(
                        str(message_record['id']),
                        'FAILED',
                        error_message=result.get('error_message', 'Workflow execution failed')
                    )
                    await VendorEndpointRepository.increment_message_stats(
                        vendor_endpoint['id'],
                        received=False,
                        processed=False,
                        failed=True
                    )
            else:
                # No workflow connected, mark as received but unprocessed
                await HL7MessageRepository.update_message_status(
                    str(message_record['id']),
                    'RECEIVED'
                )

        except Exception as e:
            print(f"Error processing message in queue: {str(e)}")
            # Update message as failed
            try:
                await HL7MessageRepository.update_message_status(
                    str(message_data['message_record']['id']),
                    'FAILED',
                    error_message=str(e)
                )
                await VendorEndpointRepository.increment_message_stats(
                    message_data['vendor_endpoint']['id'],
                    received=False,
                    processed=False,
                    failed=True
                )
            except Exception as update_error:
                print(f"Error updating failed message status: {str(update_error)}")

# Global message processing queue
message_queue = MessageProcessingQueue()

async def authenticate_vendor_request(
    vendor_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """Authenticate vendor API request"""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header"
        )

    api_key = credentials.credentials

    # Find vendor endpoint by slug and verify API key
    # Note: We need tenant context - for now, we'll search across all tenants
    # In production, you might want to add tenant identification to the URL
    vendor_endpoints = await VendorEndpointRepository.search_endpoints_by_slug(vendor_slug)

    if not vendor_endpoints:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor endpoint not found"
        )

    # Find the endpoint with matching API key
    vendor_endpoint = None
    for endpoint in vendor_endpoints:
        if endpoint.get('api_key') == api_key:
            vendor_endpoint = endpoint
            break

    if not vendor_endpoint:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )

    if not vendor_endpoint.get('is_active'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vendor endpoint is not active"
        )

    return vendor_endpoint

@router.post("/{vendor_slug}/hl7/ingest", response_model=HL7IngestionResponse)
async def ingest_hl7_message(
    vendor_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    vendor_endpoint: Dict[str, Any] = Depends(authenticate_vendor_request)
):
    """
    Ingest HL7 message from EMR vendor

    This endpoint:
    1. Receives HL7 message from EMR
    2. Validates and stores the message
    3. Returns immediate acknowledgment to EMR
    4. Triggers connected workflow in background
    5. Handles high-volume processing via queue
    """
    try:
        # Get raw message body
        message_body = await request.body()
        hl7_message = message_body.decode('utf-8') if message_body else ""

        if not hl7_message.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty message body"
            )

        # Check rate limiting
        rate_limit_info = await VendorEndpointRepository.check_rate_limit(vendor_endpoint['id'])
        if rate_limit_info.get('rate_limit_exceeded'):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. {rate_limit_info.get('messages_remaining', 0)} messages remaining this hour."
            )

        # Parse the HL7 message
        try:
            parsed_hl7 = hl7_parser.parse_message(hl7_message)

            # Determine if this message should be ignored before storing
            msg_type = getattr(parsed_hl7, 'message_type', None)
            ignore_list = [
                (t or '').strip().upper() for t in _normalize_list_field(vendor_endpoint.get('ignored_message_types'))
            ]
            is_ignored = bool(msg_type) and msg_type.upper() in set(ignore_list)

            # Store the message with parsed data
            message_record = await HL7MessageRepository.create_message(
                tenant_id=vendor_endpoint['tenant_id'],
                raw_message=hl7_message,
                message_type=parsed_hl7.message_type,
                source_endpoint=f"{vendor_endpoint['vendor_name']} ({vendor_endpoint['vendor_slug']})",
                vendor_endpoint_id=vendor_endpoint['id'],
                message_direction='INBOUND',
                message_control_id=parsed_hl7.message_control_id,
                event_type=parsed_hl7.event_type,
                hl7_version=parsed_hl7.hl7_version,
                sending_application=parsed_hl7.sending_application,
                sending_facility=parsed_hl7.sending_facility,
                receiving_application=parsed_hl7.receiving_application,
                receiving_facility=parsed_hl7.receiving_facility,
                parsed_message=json.dumps(parsed_hl7.to_dict()),
                encoding_characters=parsed_hl7.encoding_chars,
                field_separator=parsed_hl7.field_separator,
                status='IGNORED' if is_ignored else 'RECEIVED'
            )
        except Exception as parse_error:
            # If parsing fails, store the message with error info
            message_record = await HL7MessageRepository.create_message(
                tenant_id=vendor_endpoint['tenant_id'],
                raw_message=hl7_message,
                message_type='PARSE_ERROR',
                source_endpoint=f"{vendor_endpoint['vendor_name']} ({vendor_endpoint['vendor_slug']})",
                vendor_endpoint_id=vendor_endpoint['id'],
                message_direction='INBOUND',
                status='FAILED'
            )

        # Update vendor stats (message received)
        await VendorEndpointRepository.increment_message_stats(
            vendor_endpoint['id'],
            received=True,
            processed=False,
            failed=False
        )

        # Determine if message type should be ignored for workflow execution (reuse logic)
        msg_type = getattr(parsed_hl7, 'message_type', None)
        ignore_list = [
            (t or '').strip().upper() for t in _normalize_list_field(vendor_endpoint.get('ignored_message_types'))
        ]
        is_ignored = bool(msg_type) and msg_type.upper() in set(ignore_list)

        # Add to processing queue for background workflow execution (unless ignored)
        workflow_triggered = bool(vendor_endpoint.get('trigger_workflow_id')) and not is_ignored
        execution_id = None

        if workflow_triggered:
            # Add to queue for background processing
            await message_queue.add_message({
                'vendor_endpoint': vendor_endpoint,
                'hl7_message': hl7_message,
                'message_record': message_record
            })
            execution_id = str(uuid.uuid4())  # Generate execution ID for tracking

        # Return immediate acknowledgment (200 OK)
        return HL7IngestionResponse(
            message="HL7 message received successfully" if not is_ignored else "HL7 message ignored by filter",
            message_id=str(message_record['id']),
            status="IGNORED" if is_ignored else "RECEIVED",
            received_at=message_record['created_at'],
            workflow_triggered=workflow_triggered,
            ignored_by_filter=is_ignored,
            execution_id=execution_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process HL7 message: {str(e)}"
        )

@router.get("/{vendor_slug}/hl7/status", response_model=Dict[str, Any])
async def get_vendor_status(
    vendor_slug: str,
    vendor_endpoint: Dict[str, Any] = Depends(authenticate_vendor_request)
):
    """Get status and statistics for a vendor endpoint"""
    try:
        # Get rate limit info
        rate_limit_info = await VendorEndpointRepository.check_rate_limit(vendor_endpoint['id'])

        # Get recent activity
        recent_activity = await VendorEndpointRepository.get_endpoint_activity(
            vendor_endpoint['id'],
            hours=24
        )

        return {
            "vendor_slug": vendor_slug,
            "vendor_name": vendor_endpoint['vendor_name'],
            "status": "active" if vendor_endpoint['is_active'] else "inactive",
            "workflow_connected": bool(vendor_endpoint.get('trigger_workflow_id')),
            "rate_limit": {
                "messages_per_hour": vendor_endpoint['rate_limit_per_hour'],
                "messages_remaining": rate_limit_info.get('messages_remaining', 0),
                "messages_used_last_hour": rate_limit_info.get('messages_last_hour', 0),
                "rate_limit_exceeded": rate_limit_info.get('rate_limit_exceeded', False)
            },
            "statistics": {
                "total_messages_received": vendor_endpoint.get('total_messages_received', 0),
                "total_messages_processed": vendor_endpoint.get('total_messages_processed', 0),
                "total_messages_failed": vendor_endpoint.get('total_messages_failed', 0)
            },
            "recent_activity": recent_activity[:24]  # Last 24 hours
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get vendor status: {str(e)}"
        )

@router.post("/{vendor_slug}/hl7/test", response_model=HL7IngestionResponse)
async def test_hl7_endpoint(
    vendor_slug: str,
    request: Request,
    vendor_endpoint: Dict[str, Any] = Depends(authenticate_vendor_request)
):
    """Test endpoint for HL7 message ingestion - same as ingest but with test flag"""
    try:
        # Get raw message body
        message_body = await request.body()
        hl7_message = message_body.decode('utf-8') if message_body else ""

        if not hl7_message.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty message body"
            )

        # Parse the HL7 message
        try:
            parsed_hl7 = hl7_parser.parse_message(hl7_message)

            # Determine if this message should be ignored before storing
            msg_type = getattr(parsed_hl7, 'message_type', None)
            ignore_list = [
                (t or '').strip().upper() for t in _normalize_list_field(vendor_endpoint.get('ignored_message_types'))
            ]
            is_ignored = bool(msg_type) and msg_type.upper() in set(ignore_list)

            # Store the message with test flag and parsed data
            message_record = await HL7MessageRepository.create_message(
                tenant_id=vendor_endpoint['tenant_id'],
                raw_message=hl7_message,
                message_type=parsed_hl7.message_type,
                source_endpoint=f"{vendor_endpoint['vendor_name']} ({vendor_endpoint['vendor_slug']}) - TEST",
                vendor_endpoint_id=vendor_endpoint['id'],
                message_direction='INBOUND',
                is_test=True,
                message_control_id=parsed_hl7.message_control_id,
                event_type=parsed_hl7.event_type,
                hl7_version=parsed_hl7.hl7_version,
                sending_application=parsed_hl7.sending_application,
                sending_facility=parsed_hl7.sending_facility,
                receiving_application=parsed_hl7.receiving_application,
                receiving_facility=parsed_hl7.receiving_facility,
                parsed_message=json.dumps(parsed_hl7.to_dict()),
                encoding_characters=parsed_hl7.encoding_chars,
                field_separator=parsed_hl7.field_separator,
                status='IGNORED' if is_ignored else 'RECEIVED'
            )
        except Exception as parse_error:
            # If parsing fails, store the message with error info
            message_record = await HL7MessageRepository.create_message(
                tenant_id=vendor_endpoint['tenant_id'],
                raw_message=hl7_message,
                message_type='PARSE_ERROR',
                source_endpoint=f"{vendor_endpoint['vendor_name']} ({vendor_endpoint['vendor_slug']}) - TEST",
                vendor_endpoint_id=vendor_endpoint['id'],
                message_direction='INBOUND',
                is_test=True,
                status='FAILED'
            )

        # Don't update vendor stats for test messages

        # Determine if message type should be ignored for workflow execution
        msg_type = None
        try:
            msg_type = getattr(parsed_hl7, 'message_type', None)
        except Exception:
            msg_type = None
        ignore_list = [
            (t or '').strip().upper() for t in _normalize_list_field(vendor_endpoint.get('ignored_message_types'))
        ]
        is_ignored = bool(msg_type) and msg_type.upper() in set(ignore_list)

        # Trigger workflow if connected (in test mode) and not ignored
        workflow_triggered = bool(vendor_endpoint.get('trigger_workflow_id')) and not is_ignored
        execution_id = None

        if workflow_triggered:
            try:
                workflow_service = WorkflowExecutionService()

                result = await workflow_service.test_workflow(
                    workflow_id=str(vendor_endpoint['trigger_workflow_id']),
                    test_data={
                        'hl7_message': hl7_message,
                        'vendor_slug': vendor_endpoint['vendor_slug'],
                        'vendor_name': vendor_endpoint['vendor_name'],
                        'message_id': str(message_record['id']),
                        'received_at': message_record['created_at'].isoformat(),
                        'test_mode': True
                    },
                    tenant_id=str(vendor_endpoint['tenant_id']),
                    user_id=None  # System triggered test
                )

                execution_id = result.get('execution_id')

                # Update test message status
                if result.get('status') == 'COMPLETED':
                    await HL7MessageRepository.update_message_status(
                        str(message_record['id']),
                        'PROCESSED',
                        processed_at=datetime.now(timezone.utc)
                    )
                else:
                    await HL7MessageRepository.update_message_status(
                        str(message_record['id']),
                        'FAILED',
                        error_message=result.get('error_message', 'Test workflow execution failed')
                    )

            except Exception as workflow_error:
                await HL7MessageRepository.update_message_status(
                    str(message_record['id']),
                    'FAILED',
                    error_message=f"Test workflow failed: {str(workflow_error)}"
                )

        return HL7IngestionResponse(
            message=(
                "Test HL7 message ignored by filter" if is_ignored else "Test HL7 message processed successfully"
            ),
            message_id=str(message_record['id']),
            status="IGNORED" if is_ignored else "TEST_COMPLETED",
            received_at=message_record['created_at'],
            workflow_triggered=workflow_triggered,
            ignored_by_filter=is_ignored,
            execution_id=execution_id
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process test HL7 message: {str(e)}"
        )
