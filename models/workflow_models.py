"""
Workflow execution models and data structures
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from services.hl7_parser import ParsedHL7Message


class ActivityStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class WorkflowContext:
    """Context passed between workflow activities"""
    workflow_id: str
    execution_id: str
    tenant_id: str
    variables: Dict[str, Any] = field(default_factory=dict)
    message: Optional[ParsedHL7Message] = None
    raw_message: Optional[str] = None
    execution_log: List[Dict[str, Any]] = field(default_factory=list)
    current_activity: Optional[str] = None
    errors: List[str] = field(default_factory=list)


@dataclass
class ActivityResult:
    """Result from activity execution"""
    status: ActivityStatus
    output_data: Dict[str, Any] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    execution_time_ms: Optional[int] = None