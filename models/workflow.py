
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, JSON, Integer, Enum, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
import enum
from .database import Base

class WorkflowStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    ARCHIVED = "ARCHIVED"

class ExecutionMode(str, enum.Enum):
    REAL_TIME = "REAL_TIME"
    QUEUED = "QUEUED"
    SCHEDULED = "SCHEDULED"

class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    version = Column(String(50), default="1.0.0")
    status = Column(Enum(WorkflowStatus), default=WorkflowStatus.DRAFT)
    execution_mode = Column(Enum(ExecutionMode), default=ExecutionMode.REAL_TIME)
    
    settings = Column(JSON, default=dict)
    environment_variables = Column(JSON, default=dict)
    
    max_concurrent_executions = Column(Integer, default=1)
    timeout_seconds = Column(Integer, default=300)
    retry_attempts = Column(Integer, default=3)
    
    cron_expression = Column(String(100), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    
    trigger_endpoint_id = Column(UUID(as_uuid=True), ForeignKey("vendor_endpoints.id"), nullable=True)
    
    total_executions = Column(Integer, default=0)
    successful_executions = Column(Integer, default=0)
    failed_executions = Column(Integer, default=0)
    avg_execution_time_ms = Column(Float, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_executed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    tenant = relationship("Tenant")
    created_by = relationship("User")
    trigger_endpoint = relationship("VendorEndpoint")
    activities = relationship("WorkflowActivity", back_populates="workflow", cascade="all, delete-orphan")
    messages = relationship("HL7Message", back_populates="workflow")

class OnErrorAction(str, enum.Enum):
    STOP = "stop"
    RETRY = "retry"
    CONTINUE = "continue"

class ActivityType(str, enum.Enum):
    # Sender Activities
    TCP_SENDER = "tcp_sender"
    HTTP_SENDER = "http_sender"
    DATABASE_WRITE = "database_write"
    FILE_WRITER = "file_writer"
    EMAIL_SENDER = "email_sender"
    
    # Transformation Activities
    MESSAGE_TRANSFORMER = "message_transformer"
    FORMAT_CONVERTER = "format_converter"
    DATA_MAPPER = "data_mapper"
    VALIDATION = "validation"
    
    # HL7 Specific Activities (per use case)
    HL7_PARSER = "hl7_parser"
    HL7_TRANSFORMER = "hl7_transformer"  
    HL7_TO_FHIR = "hl7_to_fhir"
    HL7_TO_CSV = "hl7_to_csv"
    SEGMENT_LOOP = "segment_loop"
    
    # Control Flow Activities
    CONDITION = "condition"
    LOOP = "loop"
    DELAY = "delay"
    
    # Custom Activities
    CUSTOM_CODE = "custom_code"

class TransformerType(str, enum.Enum):
    MAPPING = "mapping"
    VARIABLE = "variable"
    CUSTOM = "custom"
    CONDITIONAL = "conditional"
    LOOP = "loop"
    APPEND_SEGMENT = "append_segment"
    COMMENT = "comment"
    SET_VARIABLE = "set_variable"
    APPEND_LINE = "append_line"

class MessageFormat(str, enum.Enum):
    HL7 = "hl7"
    FHIR = "fhir"
    JSON = "json"
    XML = "xml"
    CSV = "csv"
    TEXT = "text"
    BINARY = "binary"

class WorkflowActivity(Base):
    __tablename__ = "workflow_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False, index=True)
    
    name = Column(String(200), nullable=False)
    activity_type = Column(Enum(ActivityType), nullable=False)
    order = Column(Integer, nullable=False)
    
    # Message processing configuration
    input_format = Column(Enum(MessageFormat), nullable=True)
    output_format = Column(Enum(MessageFormat), nullable=True)
    
    # Connection settings for TCP/HTTP activities
    connection_settings = Column(JSON, default=dict)
    
    config = Column(JSON, default=dict)
    is_enabled = Column(Boolean, default=True)
    on_error_action = Column(Enum(OnErrorAction), default=OnErrorAction.STOP)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    workflow = relationship("Workflow", back_populates="activities")
    transformers = relationship("ActivityTransformer", back_populates="activity", cascade="all, delete-orphan")
    executions = relationship("ActivityExecution", back_populates="activity")

class ActivityTransformer(Base):
    __tablename__ = "activity_transformers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_id = Column(UUID(as_uuid=True), ForeignKey("workflow_activities.id"), nullable=False, index=True)
    
    name = Column(String(200), nullable=False)
    transformer_type = Column(Enum(TransformerType), nullable=False)
    order = Column(Integer, nullable=False)
    
    # Transformation configuration
    source_path = Column(String(500), nullable=True)  # e.g., "MSH.3", "PID.5.1"
    target_path = Column(String(500), nullable=True)
    transformation_logic = Column(Text, nullable=True)  # Custom code or mapping rules
    
    # Variable and condition settings
    variable_name = Column(String(100), nullable=True)
    condition_expression = Column(Text, nullable=True)
    default_value = Column(String(500), nullable=True)
    
    # Loop configuration
    loop_source = Column(String(500), nullable=True)
    loop_target = Column(String(500), nullable=True)
    
    config = Column(JSON, default=dict)
    is_enabled = Column(Boolean, default=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    activity = relationship("WorkflowActivity", back_populates="transformers")

class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False, index=True)
    message_id = Column(UUID(as_uuid=True), ForeignKey("hl7_messages.id"), nullable=True, index=True)
    
    status = Column(String(50), nullable=False)  # RUNNING, COMPLETED, FAILED, CANCELLED
    
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)
    
    # Context and variables for this execution
    execution_context = Column(JSON, default=dict)
    variables = Column(JSON, default=dict)
    
    # Relationships
    workflow = relationship("Workflow")
    message = relationship("HL7Message")
    activity_executions = relationship("ActivityExecution", back_populates="workflow_execution", cascade="all, delete-orphan")

class ActivityExecution(Base):
    __tablename__ = "activity_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_execution_id = Column(UUID(as_uuid=True), ForeignKey("workflow_executions.id"), nullable=False, index=True)
    activity_id = Column(UUID(as_uuid=True), ForeignKey("workflow_activities.id"), nullable=False, index=True)
    
    status = Column(String(50), nullable=False)  # PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
    
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    
    error_message = Column(Text, nullable=True)
    logs = Column(JSON, default=list)  # Execution logs
    
    # Relationships
    workflow_execution = relationship("WorkflowExecution", back_populates="activity_executions")
    activity = relationship("WorkflowActivity", back_populates="executions")

class WorkflowRepository:
    @staticmethod
    async def create_workflow(**workflow_data):
        """Create a new workflow"""
        from database.connection import execute, fetch_one
        import uuid
        from datetime import datetime, timezone
        
        workflow_id = uuid.uuid4()
        query = """
        INSERT INTO workflows (
            id, tenant_id, created_by_id, name, description, version, 
            status, execution_mode, settings, environment_variables,
            max_concurrent_executions, timeout_seconds, retry_attempts,
            created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        RETURNING *
        """
        
        import json
        
        result = await execute(
            query,
            workflow_id,
            workflow_data.get('tenant_id'),
            workflow_data.get('created_by_id'),
            workflow_data.get('name'),
            workflow_data.get('description'),
            workflow_data.get('version', '1.0.0'),
            workflow_data.get('status', 'DRAFT'),
            workflow_data.get('execution_mode', 'REAL_TIME'),
            json.dumps(workflow_data.get('settings', {})),
            json.dumps(workflow_data.get('environment_variables', {})),
            workflow_data.get('max_concurrent_executions', 1),
            workflow_data.get('timeout_seconds', 300),
            workflow_data.get('retry_attempts', 3),
            datetime.now(timezone.utc)
        )
        
        # Fetch the created workflow
        fetch_query = "SELECT * FROM workflows WHERE id = $1"
        return await fetch_one(fetch_query, workflow_id)
    
    @staticmethod
    async def get_workflow(workflow_id):
        """Get workflow by ID"""
        from database.connection import fetch_one
        query = "SELECT * FROM workflows WHERE id = $1"
        return await fetch_one(query, workflow_id)
    
    @staticmethod
    async def get_workflows_by_tenant(tenant_id, skip=0, limit=100):
        """Get workflows by tenant"""
        from database.connection import fetch_all
        query = """
        SELECT * FROM workflows 
        WHERE tenant_id = $1 
        ORDER BY created_at DESC 
        LIMIT $2 OFFSET $3
        """
        result = await fetch_all(query, tenant_id, limit, skip)
        print("get_workflows_by_tenant result:", result)  # Debug log
        return result
    
    @staticmethod
    async def create_activity(**activity_data):
        """Create a new workflow activity"""
        from database.connection import execute, fetch_one
        import uuid
        from datetime import datetime, timezone
        
        activity_id = uuid.uuid4()
        query = """
        INSERT INTO workflow_activities (
            id, workflow_id, name, activity_type, "order", config, 
            is_enabled, on_error_action, input_format, output_format,
            connection_settings, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING *
        """
        
        import json
        
        result = await execute(
            query,
            activity_id,
            activity_data.get('workflow_id'),
            activity_data.get('name'),
            activity_data.get('activity_type'),
            activity_data.get('order'),
            json.dumps(activity_data.get('config', {})),
            activity_data.get('is_enabled', True),
            activity_data.get('on_error_action', 'STOP'),
            activity_data.get('input_format'),
            activity_data.get('output_format'),
            json.dumps(activity_data.get('connection_settings', {})),
            datetime.now(timezone.utc)
        )
        
        # Fetch the created activity
        fetch_query = "SELECT * FROM workflow_activities WHERE id = $1"
        return await fetch_one(fetch_query, activity_id)
    
    @staticmethod
    async def get_workflow_activities(workflow_id):
        """Get activities for a workflow"""
        from database.connection import fetch_all
        query = """
        SELECT * FROM workflow_activities 
        WHERE workflow_id = $1 
        ORDER BY "order"
        """
        return await fetch_all(query, workflow_id)
    
    @staticmethod
    async def create_transformer(**transformer_data):
        """Create a new activity transformer"""
        from database.connection import execute, fetch_one
        import uuid
        from datetime import datetime, timezone
        
        transformer_id = uuid.uuid4()
        query = """
        INSERT INTO activity_transformers (
            id, activity_id, name, transformer_type, "order", 
            source_path, target_path, transformation_logic,
            variable_name, condition_expression, default_value,
            loop_source, loop_target, config, is_enabled, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        RETURNING *
        """
        
        import json
        
        result = await execute(
            query,
            transformer_id,
            transformer_data.get('activity_id'),
            transformer_data.get('name'),
            transformer_data.get('transformer_type'),
            transformer_data.get('order'),
            transformer_data.get('source_path'),
            transformer_data.get('target_path'),
            transformer_data.get('transformation_logic'),
            transformer_data.get('variable_name'),
            transformer_data.get('condition_expression'),
            transformer_data.get('default_value'),
            transformer_data.get('loop_source'),
            transformer_data.get('loop_target'),
            json.dumps(transformer_data.get('config', {})),
            transformer_data.get('is_enabled', True),
            datetime.now(timezone.utc)
        )
        
        # Fetch the created transformer
        fetch_query = "SELECT * FROM activity_transformers WHERE id = $1"
        return await fetch_one(fetch_query, transformer_id)
    
    @staticmethod
    async def get_activity_transformers(activity_id):
        """Get transformers for an activity"""
        from database.connection import fetch_all
        query = """
        SELECT * FROM activity_transformers 
        WHERE activity_id = $1 
        ORDER BY "order"
        """
        return await fetch_all(query, activity_id)
