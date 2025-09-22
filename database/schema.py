"""
Database schema creation using raw SQL for reliability
"""
from .connection import execute

# Schema creation SQL
CREATE_TABLES_SQL = """
-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enums
CREATE TYPE tenant_plan AS ENUM ('FREE', 'PROFESSIONAL', 'ENTERPRISE');
CREATE TYPE database_type AS ENUM ('SHARED', 'DEDICATED'); 
CREATE TYPE user_role AS ENUM ('SUPER_ADMIN', 'TENANT_ADMIN', 'WORKFLOW_ADMIN', 'ANALYST', 'VIEWER');
CREATE TYPE workflow_status AS ENUM ('DRAFT', 'ACTIVE', 'PAUSED', 'STOPPED', 'ERROR', 'ARCHIVED');
CREATE TYPE execution_mode AS ENUM ('REAL_TIME', 'QUEUED', 'SCHEDULED');
CREATE TYPE message_status AS ENUM ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED', 'ARCHIVED', 'IGNORED');
CREATE TYPE message_direction AS ENUM ('INBOUND', 'OUTBOUND', 'INTERNAL');

-- Tenants table
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(200) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    domain VARCHAR(255),
    plan tenant_plan DEFAULT 'PROFESSIONAL',
    is_active BOOLEAN DEFAULT true,
    database_type database_type DEFAULT 'SHARED',
    database_url VARCHAR(500),
    sso_enabled BOOLEAN DEFAULT false,
    saml_config JSONB,
    oauth_config JSONB,
    billing_email VARCHAR(255),
    billing_address TEXT,
    api_key VARCHAR(100) UNIQUE NOT NULL,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    email VARCHAR(255) UNIQUE NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    password_hash VARCHAR(255),
    auth_provider VARCHAR(50) DEFAULT 'LOCAL',
    external_id VARCHAR(255),
    role user_role DEFAULT 'VIEWER',
    permissions JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT true,
    is_verified BOOLEAN DEFAULT false,
    email_verified_at TIMESTAMP WITH TIME ZONE,
    last_login_at TIMESTAMP WITH TIME ZONE,
    login_count INTEGER DEFAULT 0,
    avatar_url VARCHAR(500),
    timezone VARCHAR(100) DEFAULT 'UTC',
    preferences JSONB DEFAULT '{}',
    two_factor_enabled BOOLEAN DEFAULT false,
    two_factor_secret VARCHAR(32),
    backup_codes JSONB DEFAULT '[]',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Vendor endpoints table
CREATE TABLE IF NOT EXISTS vendor_endpoints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    vendor_slug VARCHAR(100) NOT NULL,
    vendor_name VARCHAR(200) NOT NULL,
    vendor_description TEXT,
    vendor_contact_email VARCHAR(255),
    vendor_contact_phone VARCHAR(50),
    api_key VARCHAR(255) UNIQUE NOT NULL,
    message_format VARCHAR(50) DEFAULT 'hl7',
    max_message_size INTEGER DEFAULT 10485760,
    rate_limit_per_hour INTEGER DEFAULT 1000,
    is_active BOOLEAN DEFAULT true,
    require_ssl BOOLEAN DEFAULT true,
    allowed_ip_ranges JSONB DEFAULT '[]',
    ignored_message_types JSONB DEFAULT '[]',
    total_messages_received INTEGER DEFAULT 0,
    total_messages_processed INTEGER DEFAULT 0,
    total_messages_failed INTEGER DEFAULT 0,
    trigger_workflow_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE,
    UNIQUE(tenant_id, vendor_slug)
);

-- Workflows table  
CREATE TABLE IF NOT EXISTS workflows (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    created_by_id UUID REFERENCES users(id),
    name VARCHAR(200) NOT NULL,
    description TEXT,
    version VARCHAR(50) DEFAULT '1.0.0',
    status workflow_status DEFAULT 'DRAFT',
    execution_mode execution_mode DEFAULT 'REAL_TIME',
    settings JSONB DEFAULT '{}',
    environment_variables JSONB DEFAULT '{}',
    max_concurrent_executions INTEGER DEFAULT 1,
    timeout_seconds INTEGER DEFAULT 300,
    retry_attempts INTEGER DEFAULT 3,
    cron_expression VARCHAR(100),
    next_run_at TIMESTAMP WITH TIME ZONE,
    trigger_endpoint_id UUID REFERENCES vendor_endpoints(id),
    total_executions INTEGER DEFAULT 0,
    successful_executions INTEGER DEFAULT 0,
    failed_executions INTEGER DEFAULT 0,
    avg_execution_time_ms FLOAT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE,
    last_executed_at TIMESTAMP WITH TIME ZONE
);

-- HL7 Messages table
CREATE TABLE IF NOT EXISTS hl7_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    created_by_id UUID REFERENCES users(id),
    workflow_id UUID REFERENCES workflows(id),
    vendor_endpoint_id UUID REFERENCES vendor_endpoints(id),
    message_control_id VARCHAR(255),
    message_type VARCHAR(50) NOT NULL,
    event_type VARCHAR(50),
    hl7_version VARCHAR(10),
    raw_message TEXT NOT NULL,
    parsed_message JSONB,
    encoding_characters VARCHAR(10),
    field_separator VARCHAR(1),
    sending_application VARCHAR(255),
    sending_facility VARCHAR(255),
    receiving_application VARCHAR(255),
    receiving_facility VARCHAR(255),
    status message_status DEFAULT 'RECEIVED',
    direction message_direction DEFAULT 'INBOUND',
    processing_errors JSONB,
    validation_errors JSONB,
    english_translation JSONB,
    source_endpoint VARCHAR(255),
    destination_endpoint VARCHAR(255),
    processed_at TIMESTAMP WITH TIME ZONE,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Subscription plans table
CREATE TABLE IF NOT EXISTS subscription_plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(100) UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0,
    billing_period VARCHAR(20) DEFAULT 'monthly',
    included_messages INTEGER DEFAULT 0,
    overage_rate NUMERIC(10,5) DEFAULT 0,
    stripe_product_id VARCHAR(255),
    stripe_price_id VARCHAR(255),
    features JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Workflow Activities table
CREATE TABLE IF NOT EXISTS workflow_activities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    activity_type VARCHAR(100) NOT NULL,
    description TEXT,
    order_index INTEGER NOT NULL,
    is_enabled BOOLEAN DEFAULT true,
    config JSONB DEFAULT '{}',
    input_mapping JSONB DEFAULT '{}',
    output_mapping JSONB DEFAULT '{}',
    error_handling JSONB DEFAULT '{"action": "stop", "retry_count": 0}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Workflow Executions table
CREATE TABLE IF NOT EXISTS workflow_executions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    execution_id VARCHAR(255) UNIQUE NOT NULL,
    trigger_type VARCHAR(50),
    triggered_by VARCHAR(255),
    status VARCHAR(50) DEFAULT 'PENDING',
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    execution_time_ms INTEGER,
    execution_log JSONB DEFAULT '[]',
    debug_info JSONB DEFAULT '{}',
    result JSONB DEFAULT '{}',
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Activity Executions table
CREATE TABLE IF NOT EXISTS activity_executions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_execution_id UUID REFERENCES workflow_executions(id) ON DELETE CASCADE,
    activity_id UUID REFERENCES workflow_activities(id) ON DELETE CASCADE,
    sequence_order INTEGER,
    status VARCHAR(50) DEFAULT 'PENDING',
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    execution_time_ms INTEGER,
    input_data JSONB DEFAULT '{}',
    output_data JSONB DEFAULT '{}',
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User sessions table
CREATE TABLE IF NOT EXISTS user_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_agent TEXT,
    ip_address INET
);

-- Password reset tokens
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_agent TEXT,
    ip_address INET
);
CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_token ON password_reset_tokens(token_hash);

-- MFA reset tokens
CREATE TABLE IF NOT EXISTS mfa_reset_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_agent TEXT,
    ip_address INET
);
CREATE INDEX IF NOT EXISTS idx_mfa_reset_tokens_user ON mfa_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_mfa_reset_tokens_token ON mfa_reset_tokens(token_hash);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_vendor_endpoints_tenant_id ON vendor_endpoints(tenant_id);
CREATE INDEX IF NOT EXISTS idx_vendor_endpoints_slug ON vendor_endpoints(tenant_id, vendor_slug);
CREATE INDEX IF NOT EXISTS idx_workflows_tenant_id ON workflows(tenant_id);
CREATE INDEX IF NOT EXISTS idx_workflow_activities_workflow_id ON workflow_activities(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_activities_tenant_id ON workflow_activities(tenant_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_id ON workflow_executions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_tenant_id ON workflow_executions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_execution_id ON workflow_executions(execution_id);
CREATE INDEX IF NOT EXISTS idx_activity_executions_workflow_execution_id ON activity_executions(workflow_execution_id);
CREATE INDEX IF NOT EXISTS idx_activity_executions_activity_id ON activity_executions(activity_id);
CREATE INDEX IF NOT EXISTS idx_hl7_messages_tenant_id ON hl7_messages(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hl7_messages_workflow_id ON hl7_messages(workflow_id);
CREATE INDEX IF NOT EXISTS idx_hl7_messages_created_at ON hl7_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_user_sessions_token_hash ON user_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);

-- CSV batch buffer table
CREATE TABLE IF NOT EXISTS csv_batch_rows (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    group_key TEXT NOT NULL,
    headers JSONB NOT NULL,
    row JSONB NOT NULL,
    message_id VARCHAR(255),
    execution_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    flushed_at TIMESTAMP WITH TIME ZONE,
    flush_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_csv_batch_rows_group ON csv_batch_rows(tenant_id, workflow_id, group_key, flushed_at);

-- Billing invoices table
CREATE TABLE IF NOT EXISTS billing_invoices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL,
    external_id VARCHAR(255) UNIQUE,
    period_start TIMESTAMP WITH TIME ZONE,
    period_end TIMESTAMP WITH TIME ZONE,
    amount_cents INTEGER DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'USD',
    status VARCHAR(50) DEFAULT 'draft',
    payload JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS idx_billing_invoices_tenant ON billing_invoices(tenant_id);
CREATE INDEX IF NOT EXISTS idx_billing_invoices_external ON billing_invoices(external_id);

-- Audit logs table for HIPAA compliance
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id),
    user_email VARCHAR(255),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(100),
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    ip_address VARCHAR(45),
    user_agent TEXT,
    tenant_id UUID REFERENCES tenants(id),
    session_id VARCHAR(255),
    metadata JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_id ON audit_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_logs_status ON audit_logs(status);
"""

async def create_tables():
    """Create all database tables and ensure backward-compatible columns."""
    # Split the SQL into individual statements
    statements = [stmt.strip() for stmt in CREATE_TABLES_SQL.split(';') if stmt.strip()]

    # Run base CREATE statements (idempotent)
    for statement in statements:
        try:
            await execute(statement + ';')
        except Exception as e:
            if 'already exists' not in str(e).lower():
                print(f"Warning executing: {statement[:50]}... - {e}")

    # Lightweight migrations to align older DBs with current code
    # These are safe due to IF NOT EXISTS and can run on every startup.
    migrations = [
        # Ensure subscription_plans has Stripe integration columns
        "ALTER TABLE IF EXISTS subscription_plans ADD COLUMN IF NOT EXISTS stripe_product_id VARCHAR(255)",
        "ALTER TABLE IF EXISTS subscription_plans ADD COLUMN IF NOT EXISTS stripe_price_id VARCHAR(255)",
        # Ensure features column exists and is JSONB with default
        "ALTER TABLE IF EXISTS subscription_plans ADD COLUMN IF NOT EXISTS features JSONB DEFAULT '[]'",
        # Ensure is_active exists for plans
        "ALTER TABLE IF EXISTS subscription_plans ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true",
        # Ensure timestamps exist
        "ALTER TABLE IF EXISTS subscription_plans ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
        "ALTER TABLE IF EXISTS subscription_plans ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE",
        # Ensure vendor_endpoints has ignored_message_types
        "ALTER TABLE IF EXISTS vendor_endpoints ADD COLUMN IF NOT EXISTS ignored_message_types JSONB DEFAULT '[]'",
        # Ensure message_status enum includes IGNORED
        "DO $$ BEGIN\nIF NOT EXISTS (\n  SELECT 1 FROM pg_type t\n  JOIN pg_enum e ON t.oid = e.enumtypid\n  WHERE t.typname = 'message_status' AND e.enumlabel = 'IGNORED'\n) THEN\n  ALTER TYPE message_status ADD VALUE 'IGNORED';\nEND IF;\nEND $$",
        # Add 2FA columns to users table
        "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS two_factor_enabled BOOLEAN DEFAULT false",
        "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS two_factor_secret VARCHAR(32)",
        "ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS backup_codes JSONB DEFAULT '[]'"
    ]

    # Create user_memberships table if not exists (safe idempotent)
    try:
        await execute(
            """
            CREATE TABLE IF NOT EXISTS user_memberships (
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
                role user_role NOT NULL DEFAULT 'VIEWER',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                PRIMARY KEY (user_id, tenant_id)
            )
            """
        )
    except Exception as e:
        print(f"Warning creating user_memberships: {e}")

    for stmt in migrations:
        try:
            await execute(stmt + ';')
        except Exception as e:
            # Log but do not fail startup for non-critical migration errors
            print(f"Warning migrating: {stmt} - {e}")

async def drop_tables():
    """Drop all tables (for development/testing)"""
    drop_statements = [
        "DROP TABLE IF EXISTS user_sessions CASCADE",
        "DROP TABLE IF EXISTS hl7_messages CASCADE", 
        "DROP TABLE IF EXISTS workflows CASCADE",
        "DROP TABLE IF EXISTS vendor_endpoints CASCADE",
        "DROP TABLE IF EXISTS users CASCADE",
        "DROP TABLE IF EXISTS tenants CASCADE",
        "DROP TYPE IF EXISTS message_direction",
        "DROP TYPE IF EXISTS message_status", 
        "DROP TYPE IF EXISTS execution_mode",
        "DROP TYPE IF EXISTS workflow_status",
        "DROP TYPE IF EXISTS user_role",
        "DROP TYPE IF EXISTS database_type",
        "DROP TYPE IF EXISTS tenant_plan"
    ]
    
    for statement in drop_statements:
        try:
            await execute(statement + ';')
        except Exception as e:
            print(f"Warning dropping: {statement} - {e}")
