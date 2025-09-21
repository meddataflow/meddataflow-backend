from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, JSON, Integer, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
import enum
from .database import Base

class TransformationType(str, enum.Enum):
    FIELD_MAPPING = "field_mapping"
    CODE_MAPPING = "code_mapping"
    CUSTOM_FUNCTION = "custom_function"
    CONDITIONAL = "conditional"
    AGGREGATION = "aggregation"
    VALIDATION = "validation"

class DataType(str, enum.Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    JSON = "json"

class Transformation(Base):
    __tablename__ = "transformations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    activity_id = Column(UUID(as_uuid=True), ForeignKey("workflow_activities.id"), nullable=False, index=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    
    # Basic info
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    transformation_type = Column(Enum(TransformationType), nullable=False)
    
    # Source and target schemas
    source_schema = Column(String(100), nullable=True)  # HL7_2_5, FHIR_R4, etc.
    target_schema = Column(String(100), nullable=True)
    
    # Transformation definition
    mapping_rules = Column(JSON, default=list)  # Array of mapping rules
    custom_code = Column(Text, nullable=True)  # For custom transformations
    
    # Configuration
    settings = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)
    
    # Performance tracking
    execution_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    avg_execution_time_ms = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    activity = relationship("WorkflowActivity", back_populates="transformations")
    mappings = relationship("FieldMapping", back_populates="transformation", cascade="all, delete-orphan")
    executions = relationship("MessageTransformation", back_populates="transformation")

class FieldMapping(Base):
    __tablename__ = "field_mappings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transformation_id = Column(UUID(as_uuid=True), ForeignKey("transformations.id"), nullable=False, index=True)
    
    # Mapping definition
    source_path = Column(String(255), nullable=False)  # e.g., "PID.5.1"
    target_path = Column(String(255), nullable=False)  # e.g., "name[0].family"
    
    # Data transformation
    source_data_type = Column(Enum(DataType), default=DataType.STRING)
    target_data_type = Column(Enum(DataType), default=DataType.STRING)
    
    # Transformation rules
    transformation_function = Column(String(100), nullable=True)  # uppercase, lowercase, format_date, etc.
    transformation_params = Column(JSON, default=dict)
    default_value = Column(String(255), nullable=True)
    
    # Validation
    is_required = Column(Boolean, default=False)
    validation_rules = Column(JSON, default=list)
    
    # Conditional mapping
    condition = Column(Text, nullable=True)  # JavaScript expression
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    transformation = relationship("Transformation", back_populates="mappings")

class CodeMapping(Base):
    __tablename__ = "code_mappings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # Mapping info
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Source and target systems
    source_system = Column(String(100), nullable=False)
    target_system = Column(String(100), nullable=False)
    
    # Code mappings
    mappings = Column(JSON, default=dict)  # { "source_code": "target_code" }
    
    # Metadata
    is_active = Column(Boolean, default=True)
    version = Column(String(20), default="1.0.0")
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class TransformationTest(Base):
    __tablename__ = "transformation_tests"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transformation_id = Column(UUID(as_uuid=True), ForeignKey("transformations.id"), nullable=False, index=True)
    
    # Test info
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Test data
    input_data = Column(JSON, nullable=False)
    expected_output = Column(JSON, nullable=False)
    
    # Test results
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_result = Column(JSON, nullable=True)
    is_passing = Column(Boolean, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())