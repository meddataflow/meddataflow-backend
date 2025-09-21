from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, JSON, Integer, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
import enum
from .database import Base

class MessageTransformationStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"

class MessageTransformation(Base):
    __tablename__ = "message_transformations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    source_message_id = Column(UUID(as_uuid=True), ForeignKey("hl7_messages.id"), nullable=False, index=True)
    target_message_id = Column(UUID(as_uuid=True), ForeignKey("hl7_messages.id"), nullable=True, index=True)
    transformation_id = Column(UUID(as_uuid=True), ForeignKey("transformations.id"), nullable=False, index=True)
    
    transformation_result = Column(JSON, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    status = Column(Enum(MessageTransformationStatus), default=MessageTransformationStatus.PENDING)
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    tenant = relationship("Tenant")
    source_message = relationship("HL7Message", foreign_keys=[source_message_id])
    target_message = relationship("HL7Message", foreign_keys=[target_message_id])
    transformation = relationship("Transformation", back_populates="executions")
