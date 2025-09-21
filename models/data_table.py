from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, JSON, Integer, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
import enum
from .database import Base

class TableType(str, enum.Enum):
    SYSTEM = "system"
    CUSTOM = "custom"
    IMPORTED = "imported"

class DataTable(Base):
    __tablename__ = "data_tables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    
    # Basic info
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    table_type = Column(Enum(TableType), default=TableType.CUSTOM)
    
    # Schema definition
    schema_definition = Column(JSON, nullable=False)  # Column definitions
    
    # Configuration
    is_active = Column(Boolean, default=True)
    is_readonly = Column(Boolean, default=False)
    
    # Hierarchy support
    parent_table_id = Column(UUID(as_uuid=True), ForeignKey("data_tables.id"), nullable=True)
    hierarchy_path = Column(String(1000), nullable=True)  # For nested lookups
    
    # Metadata
    version = Column(String(20), default="1.0.0")
    tags = Column(JSON, default=list)
    
    # Statistics
    total_rows = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    tenant = relationship("Tenant", back_populates="data_tables")
    rows = relationship("DataTableRow", back_populates="table", cascade="all, delete-orphan")
    child_tables = relationship("DataTable", remote_side=[id])

class DataTableRow(Base):
    __tablename__ = "data_table_rows"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_id = Column(UUID(as_uuid=True), ForeignKey("data_tables.id"), nullable=False, index=True)
    
    # Row data
    data = Column(JSON, nullable=False)
    
    # Row metadata
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, nullable=True)
    
    # Audit fields
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    table = relationship("DataTable", back_populates="rows")

class DataTableUsage(Base):
    __tablename__ = "data_table_usage"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_id = Column(UUID(as_uuid=True), ForeignKey("data_tables.id"), nullable=False, index=True)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.id"), nullable=True, index=True)
    transformation_id = Column(UUID(as_uuid=True), ForeignKey("transformations.id"), nullable=True, index=True)
    
    # Usage info
    usage_type = Column(String(50), nullable=False)  # lookup, validation, mapping
    field_path = Column(String(255), nullable=True)
    
    # Statistics
    usage_count = Column(Integer, default=0)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())