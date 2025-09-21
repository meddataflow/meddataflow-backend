"""
HL7 Message model and repository for message processing
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid
from database.connection import fetch_one, fetch_all, execute_returning, execute

class MessageStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING" 
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"
    IGNORED = "IGNORED"

class MessageDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    INTERNAL = "INTERNAL"

class HL7MessageRepository:
    @staticmethod
    async def create_message(
        tenant_id: uuid.UUID,
        raw_message: str,
        message_type: Optional[str] = None,
        created_by_id: Optional[uuid.UUID] = None,
        workflow_id: Optional[uuid.UUID] = None,
        vendor_endpoint_id: Optional[uuid.UUID] = None,
        source_endpoint: Optional[str] = None,
        message_direction: str = "INBOUND",
        is_test: bool = False,
        **additional_fields
    ) -> Dict[str, Any]:
        """Create a new HL7 message"""
        message_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        
        query = """
        INSERT INTO hl7_messages (
            id, tenant_id, created_by_id, workflow_id, vendor_endpoint_id,
            message_control_id, message_type, event_type, hl7_version,
            raw_message, parsed_message, encoding_characters, field_separator,
            sending_application, sending_facility, receiving_application, receiving_facility,
            status, direction, source_endpoint, destination_endpoint,
            created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
            $14, $15, $16, $17, $18, $19, $20, $21, $22, $22
        )
        RETURNING *
        """
        
        return await execute_returning(
            query,
            message_id, tenant_id, created_by_id, workflow_id, vendor_endpoint_id,
            additional_fields.get('message_control_id'),
            message_type or 'UNKNOWN',
            additional_fields.get('event_type'),
            additional_fields.get('hl7_version'),
            raw_message,
            additional_fields.get('parsed_message'),
            additional_fields.get('encoding_characters'),
            additional_fields.get('field_separator'),
            additional_fields.get('sending_application'),
            additional_fields.get('sending_facility'),
            additional_fields.get('receiving_application'),
            additional_fields.get('receiving_facility'),
            additional_fields.get('status', MessageStatus.RECEIVED.value),
            message_direction,
            source_endpoint or additional_fields.get('source_endpoint'),
            additional_fields.get('destination_endpoint'),
            now
        )
    
    @staticmethod
    async def get_message_by_id(message_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get message by ID"""
        query = """
        SELECT m.*, 
               u.first_name || ' ' || u.last_name as created_by_name,
               w.name as workflow_name,
               ve.vendor_name
        FROM hl7_messages m
        LEFT JOIN users u ON m.created_by_id = u.id
        LEFT JOIN workflows w ON m.workflow_id = w.id
        LEFT JOIN vendor_endpoints ve ON m.vendor_endpoint_id = ve.id
        WHERE m.id = $1
        """
        return await fetch_one(query, message_id)
    
    @staticmethod
    async def get_messages_by_tenant(
        tenant_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        status: Optional[MessageStatus] = None,
        message_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get messages for a tenant with filters"""
        conditions = ["m.tenant_id = $1"]
        params = [tenant_id]
        param_count = 2
        
        if status:
            conditions.append(f"m.status = ${param_count}")
            params.append(status.value)
            param_count += 1
            
        if message_type:
            conditions.append(f"m.message_type = ${param_count}")
            params.append(message_type)
            param_count += 1
            
        if start_date:
            conditions.append(f"m.created_at >= ${param_count}")
            params.append(start_date)
            param_count += 1
            
        if end_date:
            conditions.append(f"m.created_at <= ${param_count}")
            params.append(end_date)
            param_count += 1
        
        where_clause = " AND ".join(conditions)
        
        query = f"""
        SELECT m.*, 
               u.first_name || ' ' || u.last_name as created_by_name,
               w.name as workflow_name,
               ve.vendor_name
        FROM hl7_messages m
        LEFT JOIN users u ON m.created_by_id = u.id
        LEFT JOIN workflows w ON m.workflow_id = w.id
        LEFT JOIN vendor_endpoints ve ON m.vendor_endpoint_id = ve.id
        WHERE {where_clause}
        ORDER BY m.created_at DESC
        LIMIT ${param_count} OFFSET ${param_count + 1}
        """
        
        params.extend([limit, offset])
        return await fetch_all(query, *params)
    
    @staticmethod
    async def update_message_status(
        message_id: uuid.UUID,
        status: MessageStatus,
        processing_errors: Optional[Dict] = None,
        validation_errors: Optional[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """Update message status and errors"""
        now = datetime.now(timezone.utc)
        
        query = """
        UPDATE hl7_messages 
        SET status = $1, processing_errors = $2, validation_errors = $3, 
            processed_at = $4, updated_at = $4
        WHERE id = $5
        RETURNING *
        """
        
        processed_at = now if status == MessageStatus.PROCESSED else None
        
        return await execute_returning(
            query, status.value, processing_errors, validation_errors,
            processed_at, message_id
        )

    @staticmethod
    async def update_message_status(
        message_id: str,
        status: str,
        error_message: Optional[str] = None,
        processed_at: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """Update message status with simple interface"""
        now = datetime.now(timezone.utc)

        query = """
        UPDATE hl7_messages
        SET status = $1, processing_errors = $2, processed_at = $3, updated_at = $4
        WHERE id = $5
        RETURNING *
        """

        # Convert string message_id to UUID
        message_uuid = uuid.UUID(message_id)

        # Set processing errors if error message provided (store as JSON string for compatibility)
        processing_errors = {"error": error_message} if error_message else None
        if processing_errors is not None:
            import json as _json
            processing_errors = _json.dumps(processing_errors)

        # Set processed_at if provided, or now if status is PROCESSED
        if not processed_at and status == "PROCESSED":
            processed_at = now

        return await execute_returning(query, status, processing_errors, processed_at, now, message_uuid)
    
    @staticmethod
    async def update_message_parsed_data(
        message_id: uuid.UUID,
        parsed_message: Dict[str, Any],
        english_translation: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Update message parsed data and English translation"""
        query = """
        UPDATE hl7_messages 
        SET parsed_message = $1, english_translation = $2, updated_at = $3
        WHERE id = $4
        RETURNING *
        """
        
        return await execute_returning(
            query, parsed_message, english_translation, 
            datetime.now(timezone.utc), message_id
        )
    
    @staticmethod
    async def get_message_stats(tenant_id: uuid.UUID) -> Dict[str, Any]:
        """Get message statistics for a tenant"""
        query = """
        SELECT 
            COUNT(*) as total_messages,
            COUNT(CASE WHEN status = 'RECEIVED' THEN 1 END) as received,
            COUNT(CASE WHEN status = 'PROCESSING' THEN 1 END) as processing,
            COUNT(CASE WHEN status = 'PROCESSED' THEN 1 END) as processed,
            COUNT(CASE WHEN status = 'FAILED' THEN 1 END) as failed,
            COUNT(CASE WHEN created_at >= CURRENT_DATE THEN 1 END) as today,
            COUNT(CASE WHEN created_at >= CURRENT_DATE - INTERVAL '7 days' THEN 1 END) as last_7_days,
            COUNT(CASE WHEN created_at >= CURRENT_DATE - INTERVAL '30 days' THEN 1 END) as last_30_days,
            COUNT(DISTINCT message_type) as unique_message_types,
            AVG(CASE WHEN processed_at IS NOT NULL AND created_at IS NOT NULL 
                THEN EXTRACT(EPOCH FROM (processed_at - created_at)) END) as avg_processing_time_seconds
        FROM hl7_messages 
        WHERE tenant_id = $1
        """
        return await fetch_one(query, tenant_id) or {}
    
    @staticmethod
    async def search_messages(
        tenant_id: uuid.UUID,
        search_term: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Search messages by content"""
        query = """
        SELECT m.*, 
               u.first_name || ' ' || u.last_name as created_by_name,
               w.name as workflow_name,
               ve.vendor_name,
               ts_rank(to_tsvector('english', raw_message), plainto_tsquery('english', $2)) as relevance
        FROM hl7_messages m
        LEFT JOIN users u ON m.created_by_id = u.id
        LEFT JOIN workflows w ON m.workflow_id = w.id
        LEFT JOIN vendor_endpoints ve ON m.vendor_endpoint_id = ve.id
        WHERE m.tenant_id = $1 
        AND (
            to_tsvector('english', raw_message) @@ plainto_tsquery('english', $2)
            OR m.message_control_id ILIKE $3
            OR m.sending_application ILIKE $3
            OR m.receiving_application ILIKE $3
        )
        ORDER BY relevance DESC, m.created_at DESC
        LIMIT $4 OFFSET $5
        """
        
        search_pattern = f"%{search_term}%"
        return await fetch_all(
            query, tenant_id, search_term, search_pattern, limit, offset
        )
    
    @staticmethod
    async def get_messages_by_workflow(workflow_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Get all messages processed by a workflow"""
        query = """
        SELECT m.*, 
               u.first_name || ' ' || u.last_name as created_by_name
        FROM hl7_messages m
        LEFT JOIN users u ON m.created_by_id = u.id
        WHERE m.workflow_id = $1
        ORDER BY m.created_at DESC
        """
        return await fetch_all(query, workflow_id)

    @staticmethod
    async def count_messages_since(tenant_id: uuid.UUID, since: datetime) -> int:
        """Count messages for tenant since a given datetime"""
        query = "SELECT COUNT(*) as count FROM hl7_messages WHERE tenant_id = $1 AND created_at >= $2"
        result = await fetch_one(query, tenant_id, since)
        return int(result['count']) if result and 'count' in result else 0

    @staticmethod
    async def count_messages_between(tenant_id: uuid.UUID, start: datetime, end: datetime) -> int:
        """Count messages for tenant between start and end inclusive"""
        query = "SELECT COUNT(*) as count FROM hl7_messages WHERE tenant_id = $1 AND created_at >= $2 AND created_at < $3"
        result = await fetch_one(query, tenant_id, start, end)
        return int(result['count']) if result and 'count' in result else 0
    
    @staticmethod
    async def delete_message(message_id: uuid.UUID) -> bool:
        """Delete a specific message"""
        query = "DELETE FROM hl7_messages WHERE id = $1"
        result = await execute(query, message_id)
        return bool(result)
    
    @staticmethod
    async def delete_old_messages(tenant_id: uuid.UUID, days_old: int = 90) -> int:
        """Delete messages older than specified days"""
        cutoff_date = datetime.now(timezone.utc) - timezone.timedelta(days=days_old)
        
        query = """
        DELETE FROM hl7_messages 
        WHERE tenant_id = $1 AND created_at < $2 AND status = 'ARCHIVED'
        """
        
        result = await execute(query, tenant_id, cutoff_date)
        # Extract number from result string like "DELETE 5"
        return int(result.split()[-1]) if result and result.split() else 0
