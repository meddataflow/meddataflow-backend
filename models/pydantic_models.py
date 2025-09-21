"""
Pydantic models for API serialization and validation
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
import uuid

# Enums
class TenantPlan(str, Enum):
    FREE = "FREE"
    PROFESSIONAL = "PROFESSIONAL"
    ENTERPRISE = "ENTERPRISE"

class DatabaseType(str, Enum):
    SHARED = "SHARED"
    DEDICATED = "DEDICATED"

class UserRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    TENANT_ADMIN = "TENANT_ADMIN"
    WORKFLOW_ADMIN = "WORKFLOW_ADMIN"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"

class WorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    ARCHIVED = "ARCHIVED"

class ExecutionMode(str, Enum):
    REAL_TIME = "REAL_TIME"
    QUEUED = "QUEUED"
    SCHEDULED = "SCHEDULED"

class MessageStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"

class MessageDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    INTERNAL = "INTERNAL"

# Base models
class BaseModelConfig(BaseModel):
    model_config = ConfigDict(from_attributes=True)

# Tenant models
class TenantBase(BaseModelConfig):
    name: str = Field(..., description="Tenant name")
    slug: str = Field(..., description="URL-friendly identifier")
    domain: Optional[str] = None
    plan: TenantPlan = TenantPlan.PROFESSIONAL
    is_active: bool = True
    database_type: DatabaseType = DatabaseType.SHARED
    database_url: Optional[str] = None
    sso_enabled: bool = False
    saml_config: Optional[Dict[str, Any]] = None
    oauth_config: Optional[Dict[str, Any]] = None
    billing_email: Optional[str] = None
    billing_address: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)

class TenantCreate(TenantBase):
    pass

class TenantUpdate(BaseModelConfig):
    name: Optional[str] = None
    domain: Optional[str] = None
    plan: Optional[TenantPlan] = None
    is_active: Optional[bool] = None
    sso_enabled: Optional[bool] = None
    saml_config: Optional[Dict[str, Any]] = None
    oauth_config: Optional[Dict[str, Any]] = None
    billing_email: Optional[str] = None
    billing_address: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None

class Tenant(TenantBase):
    id: str
    api_key: str
    created_at: datetime
    updated_at: Optional[datetime] = None

# User models
class UserBase(BaseModelConfig):
    email: str = Field(..., description="User email address")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: UserRole = UserRole.VIEWER
    permissions: List[str] = Field(default_factory=list)
    is_active: bool = True
    is_verified: bool = False
    timezone: str = "UTC"
    preferences: Dict[str, Any] = Field(default_factory=dict)

class UserCreate(UserBase):
    password: str = Field(..., min_length=6, description="User password")
    tenant_id: str

class UserUpdate(BaseModelConfig):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[UserRole] = None
    permissions: Optional[List[str]] = None
    is_active: Optional[bool] = None
    timezone: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None

class User(UserBase):
    id: str
    tenant_id: str
    auth_provider: str = "LOCAL"
    external_id: Optional[str] = None
    email_verified_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    login_count: int = 0
    avatar_url: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

class UserLogin(BaseModelConfig):
    email: str
    password: str

class UserToken(BaseModelConfig):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: User
    tenant: Dict[str, Any]

# Vendor Endpoint models
class VendorEndpointBase(BaseModelConfig):
    vendor_slug: str = Field(..., pattern="^[a-z0-9-]+$", description="URL-friendly vendor identifier")
    vendor_name: str = Field(..., description="Human-readable vendor name")
    vendor_description: Optional[str] = None
    vendor_contact_email: Optional[str] = None
    vendor_contact_phone: Optional[str] = None
    message_format: str = "hl7"
    max_message_size: int = 10485760
    rate_limit_per_hour: int = 1000
    is_active: bool = True
    require_ssl: bool = True
    allowed_ip_ranges: List[str] = Field(default_factory=list)

class VendorEndpointCreate(VendorEndpointBase):
    pass

class VendorEndpointUpdate(BaseModelConfig):
    vendor_name: Optional[str] = None
    vendor_description: Optional[str] = None
    vendor_contact_email: Optional[str] = None
    vendor_contact_phone: Optional[str] = None
    is_active: Optional[bool] = None
    max_message_size: Optional[int] = None
    rate_limit_per_hour: Optional[int] = None
    require_ssl: Optional[bool] = None
    allowed_ip_ranges: Optional[List[str]] = None

class VendorEndpoint(VendorEndpointBase):
    id: str
    tenant_id: str
    total_messages_received: int = 0
    total_messages_processed: int = 0
    total_messages_failed: int = 0
    trigger_workflow_id: Optional[str] = None
    endpoint_url: Optional[str] = None
    statistics: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

# Workflow models
class WorkflowBase(BaseModelConfig):
    name: str = Field(..., description="Workflow name")
    description: Optional[str] = None
    version: str = "1.0.0"
    status: WorkflowStatus = WorkflowStatus.DRAFT
    execution_mode: ExecutionMode = ExecutionMode.REAL_TIME
    settings: Dict[str, Any] = Field(default_factory=dict)
    environment_variables: Dict[str, Any] = Field(default_factory=dict)
    max_concurrent_executions: int = 1
    timeout_seconds: int = 300
    retry_attempts: int = 3
    cron_expression: Optional[str] = None

class WorkflowCreate(WorkflowBase):
    trigger_endpoint_id: Optional[str] = None

class WorkflowUpdate(BaseModelConfig):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    status: Optional[WorkflowStatus] = None
    execution_mode: Optional[ExecutionMode] = None
    settings: Optional[Dict[str, Any]] = None
    environment_variables: Optional[Dict[str, Any]] = None
    max_concurrent_executions: Optional[int] = None
    timeout_seconds: Optional[int] = None
    retry_attempts: Optional[int] = None
    cron_expression: Optional[str] = None
    trigger_endpoint_id: Optional[str] = None

class Workflow(WorkflowBase):
    id: str
    tenant_id: str
    created_by_id: str
    next_run_at: Optional[datetime] = None
    trigger_endpoint_id: Optional[str] = None
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    avg_execution_time_ms: Optional[float] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_executed_at: Optional[datetime] = None

# Workflow Activity models
class WorkflowActivityBase(BaseModelConfig):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    
    name: str = Field(..., description="Activity name")
    activity_type: str = Field(..., description="Type of the activity")
    order: int = Field(..., description="Execution order of the activity", alias="order_index")
    config: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True

class WorkflowActivityCreate(WorkflowActivityBase):
    error_handling: Optional[Dict[str, Any]] = None
    on_error_action: Optional[str] = None

class WorkflowActivityUpdate(BaseModelConfig):
    name: Optional[str] = None
    activity_type: Optional[str] = None
    order: Optional[int] = None
    config: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None

class WorkflowActivity(WorkflowActivityBase):
    id: uuid.UUID
    workflow_id: uuid.UUID
    tenant_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    input_mapping: Optional[Dict[str, Any]] = None
    output_mapping: Optional[Dict[str, Any]] = None
    error_handling: Optional[Dict[str, Any]] = None
    on_error_action: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

# HL7 Message models
class HL7MessageBase(BaseModelConfig):
    message_control_id: Optional[str] = None
    message_type: str = Field(..., description="HL7 message type")
    event_type: Optional[str] = None
    hl7_version: Optional[str] = None
    raw_message: str = Field(..., description="Raw HL7 message content")
    parsed_message: Optional[Dict[str, Any]] = None
    encoding_characters: Optional[str] = None
    field_separator: Optional[str] = None
    sending_application: Optional[str] = None
    sending_facility: Optional[str] = None
    receiving_application: Optional[str] = None
    receiving_facility: Optional[str] = None
    status: MessageStatus = MessageStatus.RECEIVED
    direction: MessageDirection = MessageDirection.INBOUND
    processing_errors: Optional[Dict[str, Any]] = None
    validation_errors: Optional[Dict[str, Any]] = None
    english_translation: Optional[Dict[str, Any]] = None
    source_endpoint: Optional[str] = None
    destination_endpoint: Optional[str] = None

class HL7MessageCreate(HL7MessageBase):
    workflow_id: Optional[str] = None
    vendor_endpoint_id: Optional[str] = None

class HL7MessageUpdate(BaseModelConfig):
    status: Optional[MessageStatus] = None
    parsed_message: Optional[Dict[str, Any]] = None
    processing_errors: Optional[Dict[str, Any]] = None
    validation_errors: Optional[Dict[str, Any]] = None
    english_translation: Optional[Dict[str, Any]] = None
    processed_at: Optional[datetime] = None

class HL7Message(HL7MessageBase):
    id: str
    tenant_id: str
    created_by_id: Optional[str] = None
    workflow_id: Optional[str] = None
    vendor_endpoint_id: Optional[str] = None
    processed_at: Optional[datetime] = None
    retry_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

# User Session models
class UserSession(BaseModelConfig):
    id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    last_used_at: datetime
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None

# Response models for lists with pagination
class PaginationInfo(BaseModelConfig):
    total: int
    offset: int
    limit: int
    has_more: bool

class VendorEndpointList(BaseModelConfig):
    endpoints: List[VendorEndpoint]
    pagination: PaginationInfo

class WorkflowList(BaseModelConfig):
    workflows: List[Workflow]
    pagination: PaginationInfo

class HL7MessageList(BaseModelConfig):
    messages: List[HL7Message]
    pagination: PaginationInfo

class UserList(BaseModelConfig):
    users: List[User]
    pagination: PaginationInfo