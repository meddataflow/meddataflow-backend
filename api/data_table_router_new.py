"""
Data Table Router - AsyncPG Compatible
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

from api.auth_deps import get_current_user, get_current_tenant

router = APIRouter(prefix="/api/data-tables", tags=["data-tables"])

# Pydantic models
class DataTableResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    table_type: str
    schema_definition: Dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime

class DataTableCreate(BaseModel):
    name: str
    description: Optional[str] = None
    table_type: str = "custom"
    schema_definition: Dict[str, Any]

@router.get("/", response_model=List[DataTableResponse])
@router.get("", response_model=List[DataTableResponse])
async def get_data_tables(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    table_type: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get data tables for current tenant"""
    try:
        # Mock data for now - in real implementation would query database
        mock_tables = [
            {
                "id": "550e8400-e29b-41d4-a716-446655440001",
                "name": "Patient Demographics",
                "description": "Patient demographic data extracted from HL7 messages",
                "table_type": "system",
                "schema_definition": {
                    "columns": [
                        {"name": "patient_id", "type": "string", "required": True},
                        {"name": "first_name", "type": "string", "required": True},
                        {"name": "last_name", "type": "string", "required": True},
                        {"name": "date_of_birth", "type": "date", "required": False},
                        {"name": "gender", "type": "string", "required": False}
                    ]
                },
                "is_active": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            },
            {
                "id": "550e8400-e29b-41d4-a716-446655440002", 
                "name": "Lab Results",
                "description": "Laboratory test results from HL7 messages",
                "table_type": "system",
                "schema_definition": {
                    "columns": [
                        {"name": "result_id", "type": "string", "required": True},
                        {"name": "patient_id", "type": "string", "required": True},
                        {"name": "test_code", "type": "string", "required": True},
                        {"name": "test_name", "type": "string", "required": False},
                        {"name": "result_value", "type": "string", "required": False},
                        {"name": "reference_range", "type": "string", "required": False},
                        {"name": "result_date", "type": "datetime", "required": False}
                    ]
                },
                "is_active": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            },
            {
                "id": "550e8400-e29b-41d4-a716-446655440003",
                "name": "Medication Orders", 
                "description": "Medication order data from pharmacy systems",
                "table_type": "custom",
                "schema_definition": {
                    "columns": [
                        {"name": "order_id", "type": "string", "required": True},
                        {"name": "patient_id", "type": "string", "required": True},
                        {"name": "medication_name", "type": "string", "required": True},
                        {"name": "dosage", "type": "string", "required": False},
                        {"name": "frequency", "type": "string", "required": False},
                        {"name": "prescriber", "type": "string", "required": False},
                        {"name": "order_date", "type": "datetime", "required": False}
                    ]
                },
                "is_active": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
        ]
        
        # Apply filters
        filtered_tables = mock_tables
        if table_type:
            filtered_tables = [t for t in filtered_tables if t["table_type"] == table_type]
            
        # Apply pagination
        paginated_tables = filtered_tables[offset:offset + limit]
        
        return [
            DataTableResponse(
                id=table["id"],
                name=table["name"],
                description=table["description"],
                table_type=table["table_type"],
                schema_definition=table["schema_definition"],
                is_active=table["is_active"],
                created_at=table["created_at"],
                updated_at=table["updated_at"]
            )
            for table in paginated_tables
        ]
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch data tables: {str(e)}"
        )

@router.get("/{table_id}", response_model=DataTableResponse)
async def get_data_table(
    table_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get a specific data table"""
    try:
        # Mock data - in real implementation would query database
        if table_id == "550e8400-e29b-41d4-a716-446655440001":
            table = {
                "id": "550e8400-e29b-41d4-a716-446655440001",
                "name": "Patient Demographics",
                "description": "Patient demographic data extracted from HL7 messages",
                "table_type": "system",
                "schema_definition": {
                    "columns": [
                        {"name": "patient_id", "type": "string", "required": True},
                        {"name": "first_name", "type": "string", "required": True},
                        {"name": "last_name", "type": "string", "required": True},
                        {"name": "date_of_birth", "type": "date", "required": False},
                        {"name": "gender", "type": "string", "required": False}
                    ]
                },
                "is_active": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
            
            return DataTableResponse(
                id=table["id"],
                name=table["name"],
                description=table["description"],
                table_type=table["table_type"],
                schema_definition=table["schema_definition"],
                is_active=table["is_active"],
                created_at=table["created_at"],
                updated_at=table["updated_at"]
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Data table not found"
            )
            
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid table ID format"
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch data table: {str(e)}"
        )

@router.post("/", response_model=DataTableResponse)
@router.post("", response_model=DataTableResponse)
async def create_data_table(
    table_data: DataTableCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Create a new data table"""
    try:
        # Mock creation - in real implementation would insert to database
        new_table = {
            "id": str(uuid.uuid4()),
            "name": table_data.name,
            "description": table_data.description,
            "table_type": table_data.table_type,
            "schema_definition": table_data.schema_definition,
            "is_active": True,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        
        return DataTableResponse(
            id=new_table["id"],
            name=new_table["name"],
            description=new_table["description"],
            table_type=new_table["table_type"],
            schema_definition=new_table["schema_definition"],
            is_active=new_table["is_active"],
            created_at=new_table["created_at"],
            updated_at=new_table["updated_at"]
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create data table: {str(e)}"
        )