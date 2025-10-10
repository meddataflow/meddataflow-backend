"""
Vendor Endpoint model and repository for managing external vendor integrations
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import uuid
from database.connection import fetch_one, fetch_all, execute_returning, execute
import json

class VendorEndpointRepository:
    @staticmethod
    async def create_endpoint(
        tenant_id: uuid.UUID,
        vendor_slug: str,
        vendor_name: str,
        vendor_description: Optional[str] = None,
        vendor_contact_email: Optional[str] = None,
        vendor_contact_phone: Optional[str] = None,
        api_key: Optional[str] = None,
        message_format: str = "hl7",
        max_message_size: int = 10485760,  # 10MB
        rate_limit_per_hour: int = 1000
    ) -> Dict[str, Any]:
        """Create a new vendor endpoint"""
        endpoint_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Use a dict for fields and values, then build query dynamically
        fields = [
            "id", "tenant_id", "vendor_slug", "vendor_name", "vendor_description",
            "vendor_contact_email", "vendor_contact_phone", "api_key", "message_format",
            "max_message_size", "rate_limit_per_hour", "is_active", "require_ssl",
            "allowed_ip_ranges", "ignored_message_types", "total_messages_received", "total_messages_processed",
            "total_messages_failed", "created_at", "updated_at",
            "ack_on_receive", "ack_profile"
        ]
        values = [
            endpoint_id, tenant_id, vendor_slug, vendor_name, vendor_description,
            vendor_contact_email, vendor_contact_phone, api_key, message_format,
            max_message_size, rate_limit_per_hour, True, True,
            json.dumps([]), json.dumps([]), 0, 0, 0, now, now,
            False, 'default'
        ]
        placeholders = [f"${i+1}" for i in range(len(fields))]
        query = f"""
        INSERT INTO vendor_endpoints (
            {', '.join(fields)}
        ) VALUES (
            {', '.join(placeholders)}
        )
        RETURNING *
        """
        return await execute_returning(query, *values)
    
    @staticmethod
    async def get_endpoint_by_id(endpoint_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get vendor endpoint by ID"""
        query = """
        SELECT ve.*, 
               t.name as tenant_name,
               w.name as trigger_workflow_name,
               COUNT(m.id) as message_count,
               COUNT(CASE WHEN m.created_at >= CURRENT_DATE - INTERVAL '24 hours' THEN 1 END) as messages_last_24h,
               COUNT(m.id) as computed_total_messages,
               COUNT(CASE WHEN m.status = 'PROCESSED' THEN 1 END) as computed_processed_messages,
               COUNT(CASE WHEN m.status = 'FAILED' THEN 1 END) as computed_failed_messages,
               COUNT(CASE WHEN m.status = 'RECEIVED' THEN 1 END) as computed_received_messages,
               COUNT(CASE WHEN m.status = 'IGNORED' THEN 1 END) as computed_ignored_messages
        FROM vendor_endpoints ve
        LEFT JOIN tenants t ON ve.tenant_id = t.id
        LEFT JOIN workflows w ON ve.trigger_workflow_id = w.id
        LEFT JOIN hl7_messages m ON ve.id = m.vendor_endpoint_id
        WHERE ve.id = $1
        GROUP BY ve.id, t.name, w.name
        """
        return await fetch_one(query, endpoint_id)
    
    @staticmethod
    async def get_endpoint_by_slug(tenant_id: uuid.UUID, vendor_slug: str) -> Optional[Dict[str, Any]]:
        """Get vendor endpoint by tenant and slug"""
        query = """
        SELECT ve.*, 
               t.name as tenant_name,
               w.name as trigger_workflow_name
        FROM vendor_endpoints ve
        LEFT JOIN tenants t ON ve.tenant_id = t.id
        LEFT JOIN workflows w ON ve.trigger_workflow_id = w.id
        WHERE ve.tenant_id = $1 AND ve.vendor_slug = $2 AND ve.is_active = true
        """
        return await fetch_one(query, tenant_id, vendor_slug)
    
    @staticmethod
    async def get_endpoints_by_tenant(
        tenant_id: uuid.UUID,
        is_active: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get vendor endpoints for a tenant"""
        conditions = ["ve.tenant_id = $1"]
        params = [tenant_id]
        param_count = 2
        
        if is_active is not None:
            conditions.append(f"ve.is_active = ${param_count}")
            params.append(is_active)
            param_count += 1
        
        where_clause = " AND ".join(conditions)
        
        query = f"""
        SELECT ve.*, 
               w.name as trigger_workflow_name,
               COUNT(m.id) as message_count,
               COUNT(m.id) as computed_total_messages,
               COUNT(CASE WHEN m.status = 'PROCESSED' THEN 1 END) as computed_processed_messages,
               COUNT(CASE WHEN m.status = 'FAILED' THEN 1 END) as computed_failed_messages,
               COUNT(CASE WHEN m.status = 'RECEIVED' THEN 1 END) as computed_received_messages,
               COUNT(CASE WHEN m.status = 'IGNORED' THEN 1 END) as computed_ignored_messages,
               COUNT(CASE WHEN m.created_at >= CURRENT_DATE - INTERVAL '7 days' THEN 1 END) as recent_messages,
               MAX(m.created_at) as last_message_at
        FROM vendor_endpoints ve
        LEFT JOIN workflows w ON ve.trigger_workflow_id = w.id
        LEFT JOIN hl7_messages m ON ve.id = m.vendor_endpoint_id
        WHERE {where_clause}
        GROUP BY ve.id, w.name
        ORDER BY ve.updated_at DESC
        LIMIT ${param_count} OFFSET ${param_count + 1}
        """
        
        params.extend([limit, offset])
        return await fetch_all(query, *params)
    
    @staticmethod
    async def update_endpoint(endpoint_id: uuid.UUID, **updates) -> Optional[Dict[str, Any]]:
        """Update vendor endpoint fields"""
        if not updates:
            return None
        
        # Build dynamic update query
        set_clauses = []
        values = []
        param_count = 1
        
        allowed_fields = [
            'vendor_slug', 'vendor_name', 'vendor_description', 'vendor_contact_email',
            'vendor_contact_phone', 'api_key', 'message_format', 'max_message_size',
            'rate_limit_per_hour', 'is_active', 'require_ssl', 'allowed_ip_ranges',
            'ignored_message_types', 'ack_on_receive', 'ack_profile',
            'trigger_workflow_id'
        ]
        
        for field, value in updates.items():
            if field in allowed_fields:
                # Coerce JSONB fields to JSON strings for broad DB compatibility
                if field in ('allowed_ip_ranges', 'ignored_message_types'):
                    if isinstance(value, (list, dict)):
                        value = json.dumps(value)
                set_clauses.append(f"{field} = ${param_count}")
                values.append(value)
                param_count += 1
        
        if not set_clauses:
            return None
        
        set_clauses.append(f"updated_at = ${param_count}")
        values.append(datetime.now(timezone.utc))
        param_count += 1
        
        values.append(endpoint_id)  # for WHERE clause
        
        query = f"""
        UPDATE vendor_endpoints 
        SET {', '.join(set_clauses)}
        WHERE id = ${param_count}
        RETURNING *
        """
        
        return await execute_returning(query, *values)
    
    @staticmethod
    async def increment_message_stats(
        endpoint_id: uuid.UUID,
        received: bool = True,
        processed: bool = False,
        failed: bool = False
    ) -> None:
        """Increment message statistics for endpoint"""
        updates = []
        if received:
            updates.append("total_messages_received = total_messages_received + 1")
        if processed:
            updates.append("total_messages_processed = total_messages_processed + 1")
        if failed:
            updates.append("total_messages_failed = total_messages_failed + 1")
        
        if not updates:
            return
        
        query = f"""
        UPDATE vendor_endpoints 
        SET {', '.join(updates)}, updated_at = $1
        WHERE id = $2
        """
        
        await execute(query, datetime.now(timezone.utc), endpoint_id)
    
    @staticmethod
    async def get_endpoint_stats(tenant_id: uuid.UUID) -> Dict[str, Any]:
        """Get vendor endpoint statistics for a tenant"""
        query = """
        SELECT 
            COUNT(*) as total_endpoints,
            COUNT(CASE WHEN is_active = true THEN 1 END) as active_endpoints,
            COUNT(CASE WHEN is_active = false THEN 1 END) as inactive_endpoints,
            SUM(total_messages_received) as total_messages_received,
            SUM(total_messages_processed) as total_messages_processed,
            SUM(total_messages_failed) as total_messages_failed,
            COUNT(DISTINCT message_format) as unique_message_formats,
            AVG(rate_limit_per_hour) as avg_rate_limit,
            COUNT(CASE WHEN created_at >= CURRENT_DATE - INTERVAL '7 days' THEN 1 END) as created_last_7_days
        FROM vendor_endpoints 
        WHERE tenant_id = $1
        """
        return await fetch_one(query, tenant_id) or {}
    
    @staticmethod
    async def search_endpoints(
        tenant_id: uuid.UUID,
        search_term: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Search vendor endpoints by name, slug, or description"""
        query = """
        SELECT ve.*, 
               w.name as trigger_workflow_name,
               COUNT(m.id) as message_count
        FROM vendor_endpoints ve
        LEFT JOIN workflows w ON ve.trigger_workflow_id = w.id
        LEFT JOIN hl7_messages m ON ve.id = m.vendor_endpoint_id
        WHERE ve.tenant_id = $1 
        AND (
            ve.vendor_name ILIKE $2
            OR ve.vendor_slug ILIKE $2
            OR ve.vendor_description ILIKE $2
            OR ve.vendor_contact_email ILIKE $2
        )
        GROUP BY ve.id, w.name
        ORDER BY ve.updated_at DESC
        LIMIT $3 OFFSET $4
        """
        
        search_pattern = f"%{search_term}%"
        return await fetch_all(query, tenant_id, search_pattern, limit, offset)

    @staticmethod
    async def search_endpoints_by_slug(vendor_slug: str) -> List[Dict[str, Any]]:
        """Search vendor endpoints by slug across all tenants (for API authentication)"""
        query = """
        SELECT ve.*,
               t.name as tenant_name,
               w.name as trigger_workflow_name
        FROM vendor_endpoints ve
        LEFT JOIN tenants t ON ve.tenant_id = t.id
        LEFT JOIN workflows w ON ve.trigger_workflow_id = w.id
        WHERE ve.vendor_slug = $1 AND ve.is_active = true
        """
        return await fetch_all(query, vendor_slug)
    
    @staticmethod
    async def validate_slug(tenant_id: uuid.UUID, vendor_slug: str, exclude_endpoint_id: Optional[uuid.UUID] = None) -> bool:
        """Check if vendor slug is available for tenant"""
        if exclude_endpoint_id:
            query = """
            SELECT COUNT(*) as count FROM vendor_endpoints 
            WHERE tenant_id = $1 AND vendor_slug = $2 AND id != $3
            """
            result = await fetch_one(query, tenant_id, vendor_slug, exclude_endpoint_id)
        else:
            query = """
            SELECT COUNT(*) as count FROM vendor_endpoints 
            WHERE tenant_id = $1 AND vendor_slug = $2
            """
            result = await fetch_one(query, tenant_id, vendor_slug)
        
        return result['count'] == 0 if result else True
    
    @staticmethod
    async def get_endpoint_activity(endpoint_id: uuid.UUID, hours: int = 24) -> List[Dict[str, Any]]:
        """Get recent activity for an endpoint"""
        query = """
        SELECT 
            DATE_TRUNC('hour', created_at) as hour,
            COUNT(*) as message_count,
            COUNT(CASE WHEN status = 'PROCESSED' THEN 1 END) as processed_count,
            COUNT(CASE WHEN status = 'FAILED' THEN 1 END) as failed_count,
            AVG(CASE WHEN processed_at IS NOT NULL AND created_at IS NOT NULL 
                THEN EXTRACT(EPOCH FROM (processed_at - created_at)) END) as avg_processing_time
        FROM hl7_messages
        WHERE vendor_endpoint_id = $1 
        AND created_at >= NOW() - INTERVAL '%s hours'
        GROUP BY DATE_TRUNC('hour', created_at)
        ORDER BY hour DESC
        """ % hours
        
        return await fetch_all(query, endpoint_id)
    
    @staticmethod
    async def check_rate_limit(endpoint_id: uuid.UUID) -> Dict[str, Any]:
        """Check current rate limit status for endpoint"""
        query = """
        SELECT 
            ve.rate_limit_per_hour,
            COUNT(m.id) as messages_last_hour
        FROM vendor_endpoints ve
        LEFT JOIN hl7_messages m ON ve.id = m.vendor_endpoint_id 
            AND m.created_at >= NOW() - INTERVAL '1 hour'
        WHERE ve.id = $1
        GROUP BY ve.rate_limit_per_hour
        """
        
        result = await fetch_one(query, endpoint_id)
        if not result:
            return {"rate_limit_exceeded": False, "messages_remaining": 0}
        
        messages_remaining = max(0, result['rate_limit_per_hour'] - result['messages_last_hour'])
        rate_limit_exceeded = result['messages_last_hour'] >= result['rate_limit_per_hour']
        
        return {
            "rate_limit_exceeded": rate_limit_exceeded,
            "messages_remaining": messages_remaining,
            "messages_last_hour": result['messages_last_hour'],
            "rate_limit_per_hour": result['rate_limit_per_hour']
        }
    
    @staticmethod
    async def delete_endpoint(endpoint_id: uuid.UUID) -> bool:
        """Soft delete an endpoint (deactivate it)"""
        query = """
        UPDATE vendor_endpoints 
        SET is_active = false, updated_at = $1
        WHERE id = $2
        """
        
        result = await execute(query, datetime.now(timezone.utc), endpoint_id)
        return result and "1" in result  # Check if one row was updated
