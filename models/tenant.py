"""
Tenant model and repository for multi-tenant meddataflow platform
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid
import secrets
from database.connection import fetch_one, fetch_all, execute_returning, execute
import json

class TenantPlan(str, Enum):
    FREE = "FREE"
    PROFESSIONAL = "PROFESSIONAL"
    ENTERPRISE = "ENTERPRISE"

class DatabaseType(str, Enum):
    SHARED = "SHARED"
    DEDICATED = "DEDICATED"

class TenantRepository:
    @staticmethod
    async def create_tenant(
        name: str,
        slug: str,
        domain: Optional[str] = None,
        plan: TenantPlan | str = TenantPlan.PROFESSIONAL,
        billing_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new tenant"""
        tenant_id = uuid.uuid4()
        api_key = f"hl7_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc)
        # Coerce plan to enum value
        if isinstance(plan, str):
            try:
                plan = TenantPlan(plan.upper())
            except Exception:
                plan = TenantPlan.PROFESSIONAL
        
        query = """
        INSERT INTO tenants (
            id, name, slug, domain, plan, is_active, database_type,
            api_key, billing_email, settings, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11)
        RETURNING *
        """
        
        return await execute_returning(
            query, tenant_id, name, slug, domain, plan.value, True,
            DatabaseType.SHARED.value, api_key, billing_email, '{}', now
        )
    
    @staticmethod
    async def get_tenant_by_id(tenant_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get tenant by ID"""
        query = """
        SELECT t.*, 
               COUNT(u.id) as user_count,
               COUNT(w.id) as workflow_count
        FROM tenants t
        LEFT JOIN users u ON t.id = u.tenant_id AND u.is_active = true
        LEFT JOIN workflows w ON t.id = w.tenant_id
        WHERE t.id = $1 AND t.is_active = true
        GROUP BY t.id
        """
        return await fetch_one(query, tenant_id)

    @staticmethod
    async def get_tenant_by_id_any_status(tenant_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get tenant by ID regardless of active status (used for signup/checkout flows)."""
        query = """
        SELECT * FROM tenants WHERE id = $1
        """
        return await fetch_one(query, tenant_id)
    
    @staticmethod
    async def get_tenant_by_slug(slug: str) -> Optional[Dict[str, Any]]:
        """Get tenant by slug"""
        query = """
        SELECT t.*, 
               COUNT(u.id) as user_count,
               COUNT(w.id) as workflow_count
        FROM tenants t
        LEFT JOIN users u ON t.id = u.tenant_id AND u.is_active = true
        LEFT JOIN workflows w ON t.id = w.tenant_id
        WHERE t.slug = $1 AND t.is_active = true
        GROUP BY t.id
        """
        return await fetch_one(query, slug)
    
    @staticmethod
    async def get_tenant_by_api_key(api_key: str) -> Optional[Dict[str, Any]]:
        """Get tenant by API key"""
        query = """
        SELECT * FROM tenants 
        WHERE api_key = $1 AND is_active = true
        """
        return await fetch_one(query, api_key)
    
    @staticmethod
    async def get_tenant_by_domain(domain: str) -> Optional[Dict[str, Any]]:
        """Get tenant by domain"""
        query = """
        SELECT * FROM tenants 
        WHERE domain = $1 AND is_active = true
        """
        return await fetch_one(query, domain)
    
    @staticmethod
    async def get_all_tenants() -> List[Dict[str, Any]]:
        """Get all active tenants"""
        query = """
        SELECT t.*, 
               COUNT(u.id) as user_count,
               COUNT(w.id) as workflow_count
        FROM tenants t
        LEFT JOIN users u ON t.id = u.tenant_id AND u.is_active = true
        LEFT JOIN workflows w ON t.id = w.tenant_id
        WHERE t.is_active = true
        GROUP BY t.id
        ORDER BY t.created_at DESC
        """
        return await fetch_all(query)

    @staticmethod
    async def get_all_tenants_any_status() -> List[Dict[str, Any]]:
        """Get all tenants regardless of active status"""
        query = """
        SELECT t.*, 
               COUNT(u.id) as user_count,
               COUNT(w.id) as workflow_count
        FROM tenants t
        LEFT JOIN users u ON t.id = u.tenant_id AND u.is_active = true
        LEFT JOIN workflows w ON t.id = w.tenant_id
        GROUP BY t.id
        ORDER BY t.created_at DESC
        """
        return await fetch_all(query)

    @staticmethod
    async def get_tenants_for_user(user_id: uuid.UUID) -> List[Dict[str, Any]]:
        """List tenants that a user has access to via memberships or primary assignment."""
        # Include the user's primary tenant (users.tenant_id) and any user_memberships
        query = """
        SELECT DISTINCT t.*, um.role as membership_role
        FROM tenants t
        LEFT JOIN user_memberships um ON um.tenant_id = t.id AND um.user_id = $1
        WHERE t.is_active = true AND (
            t.id = (SELECT tenant_id FROM users WHERE id = $1)
            OR um.user_id IS NOT NULL
        )
        ORDER BY t.name
        """
        return await fetch_all(query, user_id)
    
    @staticmethod
    async def update_tenant(tenant_id: uuid.UUID, **updates) -> Optional[Dict[str, Any]]:
        """Update tenant fields"""
        if not updates:
            return None
            
        # Build dynamic update query
        set_clauses = []
        values = []
        param_count = 1
        
        allowed_fields = [
            'name', 'domain', 'plan', 'is_active', 'database_type', 
            'database_url', 'sso_enabled', 'saml_config', 'oauth_config',
            'billing_email', 'billing_address', 'settings'
        ]
        
        json_fields = {"settings", "saml_config", "oauth_config"}
        for field, value in updates.items():
            if field in allowed_fields:
                if field in json_fields and isinstance(value, (dict, list)):
                    set_clauses.append(f"{field} = ${param_count}::jsonb")
                    values.append(json.dumps(value))
                else:
                    set_clauses.append(f"{field} = ${param_count}")
                    values.append(value)
                param_count += 1
        
        if not set_clauses:
            return None
        
        set_clauses.append(f"updated_at = ${param_count}")
        values.append(datetime.now(timezone.utc))
        param_count += 1
        
        values.append(tenant_id)  # for WHERE clause
        
        query = f"""
        UPDATE tenants 
        SET {', '.join(set_clauses)}
        WHERE id = ${param_count}
        RETURNING *
        """
        
        return await execute_returning(query, *values)
    
    @staticmethod
    async def regenerate_api_key(tenant_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Regenerate API key for tenant"""
        new_api_key = f"hl7_{secrets.token_urlsafe(32)}"
        
        query = """
        UPDATE tenants 
        SET api_key = $1, updated_at = $2
        WHERE id = $3
        RETURNING *
        """
        
        return await execute_returning(
            query, new_api_key, datetime.now(timezone.utc), tenant_id
        )
    
    @staticmethod
    async def get_tenant_stats(tenant_id: uuid.UUID) -> Dict[str, Any]:
        """Get comprehensive tenant statistics"""
        stats_query = """
        SELECT 
            t.name,
            t.plan,
            t.created_at,
            COUNT(DISTINCT u.id) as total_users,
            COUNT(DISTINCT w.id) as total_workflows,
            COUNT(DISTINCT CASE WHEN w.status = 'ACTIVE' THEN w.id END) as active_workflows,
            COUNT(DISTINCT m.id) as total_messages,
            COUNT(DISTINCT CASE WHEN m.created_at >= CURRENT_DATE - INTERVAL '30 days' THEN m.id END) as messages_last_30_days,
            COUNT(DISTINCT CASE WHEN m.status = 'FAILED' THEN m.id END) as failed_messages,
            COUNT(DISTINCT ve.id) as vendor_endpoints
        FROM tenants t
        LEFT JOIN users u ON t.id = u.tenant_id AND u.is_active = true
        LEFT JOIN workflows w ON t.id = w.tenant_id
        LEFT JOIN hl7_messages m ON t.id = m.tenant_id
        LEFT JOIN vendor_endpoints ve ON t.id = ve.tenant_id AND ve.is_active = true
        WHERE t.id = $1 AND t.is_active = true
        GROUP BY t.id, t.name, t.plan, t.created_at
        """
        
        result = await fetch_one(stats_query, tenant_id)
        if not result:
            return {}
        
        # Get recent activity
        activity_query = """
        SELECT 'message' as type, created_at, status
        FROM hl7_messages 
        WHERE tenant_id = $1 AND created_at >= CURRENT_DATE - INTERVAL '7 days'
        UNION ALL
        SELECT 'workflow' as type, created_at, status::text
        FROM workflows 
        WHERE tenant_id = $1 AND created_at >= CURRENT_DATE - INTERVAL '7 days'
        ORDER BY created_at DESC
        LIMIT 10
        """
        
        recent_activity = await fetch_all(activity_query, tenant_id)
        
        return {
            **result,
            'recent_activity': recent_activity
        }
    
    @staticmethod
    async def validate_slug(slug: str, exclude_tenant_id: Optional[uuid.UUID] = None) -> bool:
        """Check if tenant slug is available"""
        if exclude_tenant_id:
            query = """
            SELECT COUNT(*) as count FROM tenants 
            WHERE slug = $1 AND id != $2
            """
            result = await fetch_one(query, slug, exclude_tenant_id)
        else:
            query = """
            SELECT COUNT(*) as count FROM tenants 
            WHERE slug = $1
            """
            result = await fetch_one(query, slug)
        
        return result['count'] == 0 if result else True
