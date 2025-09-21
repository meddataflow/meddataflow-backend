
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, JSON, Integer, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
import enum
from .database import Base

class ExecutionStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PENDING = "PENDING"
    SKIPPED = "SKIPPED"

class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    execution_id = Column(String, unique=True, nullable=False, index=True)
    
    trigger_type = Column(String(50))
    triggered_by = Column(String(255))
    
    status = Column(Enum(ExecutionStatus), default=ExecutionStatus.PENDING)
    
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    execution_time_ms = Column(Integer)
    
    execution_log = Column(JSON, default=list)
    debug_info = Column(JSON, default=dict)
    result = Column(JSON, default=dict)
    error_message = Column(Text)
    
    # Relationships
    workflow = relationship("Workflow")
    tenant = relationship("Tenant")
    activity_executions = relationship("ActivityExecution", back_populates="workflow_execution", cascade="all, delete-orphan")

class ActivityExecution(Base):
    __tablename__ = "activity_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_execution_id = Column(UUID(as_uuid=True), ForeignKey("workflow_executions.id"), nullable=False, index=True)
    activity_id = Column(UUID(as_uuid=True), ForeignKey("workflow_activities.id"), nullable=False, index=True)
    
    sequence_order = Column(Integer)
    status = Column(Enum(ExecutionStatus), default=ExecutionStatus.PENDING)
    
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    execution_time_ms = Column(Integer)
    
    input_data = Column(JSON, default=dict)
    output_data = Column(JSON, default=dict)
    error_message = Column(Text)
    
    # Relationships
    workflow_execution = relationship("WorkflowExecution", back_populates="activity_executions")
    activity = relationship("WorkflowActivity")
