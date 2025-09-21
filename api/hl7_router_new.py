"""
HL7 Router - AsyncPG Compatible
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

from models.hl7_message import HL7MessageRepository, MessageStatus, MessageDirection
from api.auth_deps import get_current_user, get_current_tenant
from services.hl7_parser import HL7Parser

router = APIRouter(prefix="/api/hl7", tags=["hl7"])

# Pydantic models
class HL7MessageResponse(BaseModel):
    id: str
    message_control_id: Optional[str] = None
    message_type: str
    event_type: Optional[str] = None
    hl7_version: Optional[str] = None
    status: str
    direction: str
    raw_message: str
    created_at: datetime
    updated_at: datetime

class HL7MessageCreate(BaseModel):
    raw_message: str
    source_endpoint: Optional[str] = None

class HL7MessageStats(BaseModel):
    total_messages: int
    received_today: int
    processed_today: int
    failed_today: int

class ParseMessageRequest(BaseModel):
    raw_message: str

class ParseMessageResponse(BaseModel):
    parsed: bool
    message_type: Optional[str] = None
    event_type: Optional[str] = None
    hl7_version: Optional[str] = None
    sending_application: Optional[str] = None
    receiving_application: Optional[str] = None
    message_control_id: Optional[str] = None
    segments: List[Dict[str, Any]] = []
    validation_errors: List[str] = []
    english_translation: List[str] = []
    segment_count: int = 0

@router.get("/messages", response_model=List[HL7MessageResponse])
async def get_messages(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    message_status: Optional[MessageStatus] = None,
    direction: Optional[MessageDirection] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get HL7 messages for current tenant"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        messages = await HL7MessageRepository.get_messages_by_tenant(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            status=message_status if message_status else None
        )
        
        return [
            HL7MessageResponse(
                id=str(msg['id']),
                message_control_id=msg.get('message_control_id'),
                message_type=msg['message_type'],
                event_type=msg.get('event_type'),
                hl7_version=msg['hl7_version'],
                status=msg['status'],
                direction=msg['direction'],
                raw_message=msg['raw_message'],
                created_at=msg['created_at'],
                updated_at=msg['updated_at']
            )
            for msg in messages
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch messages: {str(e)}"
        )

@router.get("/messages/{message_id}", response_model=HL7MessageResponse)
async def get_message(
    message_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get a specific HL7 message"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        message_uuid = uuid.UUID(message_id)
        message = await HL7MessageRepository.get_message_by_id(message_uuid)
        
        if not message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found"
            )
            
        return HL7MessageResponse(
            id=str(message['id']),
            message_control_id=message.get('message_control_id'),
            message_type=message['message_type'],
            event_type=message.get('event_type'),
            hl7_version=message['hl7_version'],
            status=message['status'],
            direction=message['direction'],
            raw_message=message['raw_message'],
            created_at=message['created_at'],
            updated_at=message['updated_at']
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid message ID format"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch message: {str(e)}"
        )

@router.post("/messages", response_model=HL7MessageResponse)
async def create_message(
    message_data: HL7MessageCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Create a new HL7 message"""
    try:
        tenant_id = current_tenant['id']
        user_id = current_user['id']
        
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
        if isinstance(user_id, str):
            user_id = uuid.UUID(user_id)
            
        # Parse the HL7 message using enhanced parser
        try:
            parser = HL7Parser()
            parsed_message = parser.parse_message(message_data.raw_message)
            
            # Extract parsed data for database storage
            message_type = parsed_message.message_type or "Unknown"
            event_type = parsed_message.event_type
            hl7_version = parsed_message.hl7_version or "2.5"
            message_control_id = parsed_message.message_control_id
            sending_application = parsed_message.sending_application
            receiving_application = parsed_message.receiving_application
            
            # Convert segments and translation to JSON for storage
            import json
            parsed_data = {
                "segments": len(parsed_message.segments),
                "field_count": sum(len(seg.fields) for seg in parsed_message.segments)
            }
            english_translation = parsed_message.english_translation
            
        except Exception as parse_error:
            # If parsing fails, store basic info
            message_type = "Unknown"
            event_type = None
            hl7_version = "2.5"
            message_control_id = None
            sending_application = None
            receiving_application = None
            parsed_data = {"parse_error": str(parse_error)}
            english_translation = [f"Failed to parse message: {str(parse_error)}"]
        
        message = await HL7MessageRepository.create_message(
            tenant_id=tenant_id,
            raw_message=message_data.raw_message,
            message_type=message_type,
            event_type=event_type,
            hl7_version=hl7_version,
            message_control_id=message_control_id,
            sending_application=sending_application,
            receiving_application=receiving_application,
            parsed_message=json.dumps(parsed_data),
            english_translation=json.dumps(english_translation),
            created_by_id=user_id,
            source_endpoint=message_data.source_endpoint,
            status=MessageStatus.RECEIVED.value,
            direction=MessageDirection.INBOUND.value
        )
        
        return HL7MessageResponse(
            id=str(message['id']),
            message_control_id=message.get('message_control_id'),
            message_type=message['message_type'],
            event_type=message.get('event_type'),
            hl7_version=message['hl7_version'],
            status=message['status'],
            direction=message['direction'],
            raw_message=message['raw_message'],
            created_at=message['created_at'],
            updated_at=message['updated_at']
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create message: {str(e)}"
        )

@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Delete a specific HL7 message"""
    try:
        message_uuid = uuid.UUID(message_id)
        
        # First check if message exists and belongs to tenant
        existing_message = await HL7MessageRepository.get_message_by_id(message_uuid)
        if not existing_message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found"
            )
            
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        if existing_message['tenant_id'] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found"
            )
        
        # Delete the message
        success = await HL7MessageRepository.delete_message(message_uuid)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete message"
            )
        
        return {"message": "Message deleted successfully"}
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid message ID format"
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete message: {str(e)}"
        )

@router.get("/stats", response_model=HL7MessageStats)
async def get_stats(
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get HL7 message statistics for current tenant"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
            
        stats = await HL7MessageRepository.get_message_stats(tenant_id)
        
        return HL7MessageStats(
            total_messages=stats.get('total_messages', 0),
            received_today=stats.get('received_today', 0),
            processed_today=stats.get('processed_today', 0),
            failed_today=stats.get('failed_today', 0)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch stats: {str(e)}"
        )

def generate_english_translation(message, message_type):
    """
    Generate English translation for HL7 message segments
    This is the core feature that makes HL7 accessible to non-technical users
    """
    english_translation = []
    
    try:
        for segment_index, segment in enumerate(message):
            if not segment or len(segment) == 0:
                continue
                
            segment_type = str(segment[0])
            translation_item = {
                "segment_index": segment_index,
                "segment_type": segment_type,
                "english_text": "",
                "hyperlinks": []
            }
            
            # MSH - Message Header
            if segment_type == "MSH":
                try:
                    sending_app = str(segment[3]) if len(segment) > 3 else "Unknown"
                    receiving_app = str(segment[5]) if len(segment) > 5 else "Unknown" 
                    msg_type = str(segment[9]) if len(segment) > 9 else "Unknown"
                    msg_control_id = str(segment[10]) if len(segment) > 10 else "Unknown"
                    
                    translation_item["english_text"] = f"This message was sent from {sending_app} to {receiving_app}. It is a {msg_type} message with control ID {msg_control_id}."
                    
                    # Add hyperlinks to specific fields
                    translation_item["hyperlinks"] = [
                        {"text": sending_app, "field_path": "MSH.3", "position": [segment_index, 3]},
                        {"text": receiving_app, "field_path": "MSH.5", "position": [segment_index, 5]},
                        {"text": msg_type, "field_path": "MSH.9", "position": [segment_index, 9]},
                        {"text": msg_control_id, "field_path": "MSH.10", "position": [segment_index, 10]}
                    ]
                except (IndexError, TypeError):
                    translation_item["english_text"] = "This is a message header with some missing information."
            
            # PID - Patient Identification
            elif segment_type == "PID":
                try:
                    patient_name = ""
                    patient_id = str(segment[3]) if len(segment) > 3 else "Unknown"
                    
                    # Parse patient name (PID.5)
                    if len(segment) > 5 and segment[5]:
                        name_parts = str(segment[5]).split('^')
                        if len(name_parts) >= 2:
                            patient_name = f"{name_parts[1]} {name_parts[0]}"  # First Last
                        else:
                            patient_name = str(segment[5])
                    
                    dob = str(segment[7]) if len(segment) > 7 else ""
                    gender = str(segment[8]) if len(segment) > 8 else ""
                    
                    # Format date of birth
                    formatted_dob = ""
                    if dob and len(dob) >= 8:
                        try:
                            formatted_dob = f"{dob[4:6]}/{dob[6:8]}/{dob[0:4]}"
                        except:
                            formatted_dob = dob
                    
                    # Format gender
                    gender_text = {"M": "male", "F": "female"}.get(gender.upper(), gender)
                    
                    if patient_name != "Unknown" and patient_name:
                        translation_item["english_text"] = f"This message is about patient {patient_name} (ID: {patient_id})."
                        if formatted_dob:
                            translation_item["english_text"] += f" The patient was born on {formatted_dob}"
                        if gender_text:
                            translation_item["english_text"] += f" and is {gender_text}."
                    else:
                        translation_item["english_text"] = f"This message is about patient with ID {patient_id}."
                    
                    translation_item["hyperlinks"] = [
                        {"text": patient_name, "field_path": "PID.5", "position": [segment_index, 5]},
                        {"text": patient_id, "field_path": "PID.3", "position": [segment_index, 3]},
                    ]
                    
                    if formatted_dob:
                        translation_item["hyperlinks"].append({"text": formatted_dob, "field_path": "PID.7", "position": [segment_index, 7]})
                    if gender_text:
                        translation_item["hyperlinks"].append({"text": gender_text, "field_path": "PID.8", "position": [segment_index, 8]})
                        
                except (IndexError, TypeError):
                    translation_item["english_text"] = "This segment contains patient identification information."
            
            # PV1 - Patient Visit
            elif segment_type == "PV1":
                try:
                    patient_class = str(segment[2]) if len(segment) > 2 else ""
                    location = str(segment[3]) if len(segment) > 3 else ""
                    attending_doctor = str(segment[7]) if len(segment) > 7 else ""
                    
                    class_text = {
                        "I": "inpatient",
                        "O": "outpatient", 
                        "E": "emergency",
                        "U": "urgent"
                    }.get(patient_class.upper(), patient_class)
                    
                    if class_text:
                        translation_item["english_text"] = f"This is an {class_text} visit."
                        if location:
                            translation_item["english_text"] += f" The patient is located at {location}."
                        if attending_doctor:
                            translation_item["english_text"] += f" The attending physician is {attending_doctor}."
                    else:
                        translation_item["english_text"] = "This segment contains patient visit information."
                        
                    translation_item["hyperlinks"] = [
                        {"text": class_text, "field_path": "PV1.2", "position": [segment_index, 2]},
                        {"text": location, "field_path": "PV1.3", "position": [segment_index, 3]},
                        {"text": attending_doctor, "field_path": "PV1.7", "position": [segment_index, 7]}
                    ]
                    
                except (IndexError, TypeError):
                    translation_item["english_text"] = "This segment contains patient visit information."
            
            # SCH - Scheduling Activity Information  
            elif segment_type == "SCH":
                try:
                    appointment_type = str(segment[6]) if len(segment) > 6 else ""
                    appointment_reason = str(segment[7]) if len(segment) > 7 else ""
                    duration = str(segment[9]) if len(segment) > 9 else ""
                    
                    translation_item["english_text"] = "This is a scheduling message."
                    if appointment_type:
                        translation_item["english_text"] += f" The appointment type is {appointment_type}."
                    if appointment_reason:
                        translation_item["english_text"] += f" The reason for the appointment is: {appointment_reason}."
                    if duration:
                        translation_item["english_text"] += f" The appointment duration is {duration} minutes."
                        
                    translation_item["hyperlinks"] = [
                        {"text": appointment_type, "field_path": "SCH.6", "position": [segment_index, 6]},
                        {"text": appointment_reason, "field_path": "SCH.7", "position": [segment_index, 7]},
                        {"text": duration, "field_path": "SCH.9", "position": [segment_index, 9]}
                    ]
                    
                except (IndexError, TypeError):
                    translation_item["english_text"] = "This segment contains scheduling information."
            
            # OBR - Observation Request
            elif segment_type == "OBR":
                try:
                    universal_service_id = str(segment[4]) if len(segment) > 4 else ""
                    requested_datetime = str(segment[6]) if len(segment) > 6 else ""
                    
                    translation_item["english_text"] = "This is a lab or diagnostic test order."
                    if universal_service_id:
                        translation_item["english_text"] += f" The test requested is: {universal_service_id}."
                    if requested_datetime:
                        translation_item["english_text"] += f" The test was requested on {requested_datetime}."
                        
                    translation_item["hyperlinks"] = [
                        {"text": universal_service_id, "field_path": "OBR.4", "position": [segment_index, 4]},
                        {"text": requested_datetime, "field_path": "OBR.6", "position": [segment_index, 6]}
                    ]
                    
                except (IndexError, TypeError):
                    translation_item["english_text"] = "This segment contains observation/test request information."
            
            # Default handling for unknown segments
            else:
                segment_descriptions = {
                    "EVN": "This segment contains event information about when and why this message was triggered.",
                    "NK1": "This segment contains information about the patient's next of kin or emergency contact.",
                    "OBX": "This segment contains observation results or test values.",
                    "AL1": "This segment contains patient allergy information.",
                    "DG1": "This segment contains diagnosis information.",
                    "RGS": "This segment contains resource group information.",
                    "AIG": "This segment contains appointment information group details.",
                    "AIL": "This segment contains location resource information for the appointment.",
                    "AIP": "This segment contains personnel resource information for the appointment."
                }
                
                translation_item["english_text"] = segment_descriptions.get(
                    segment_type, 
                    f"This segment ({segment_type}) contains healthcare information that requires specialized interpretation."
                )
            
            if translation_item["english_text"]:
                english_translation.append(translation_item)
                
    except Exception as e:
        # Fallback translation
        english_translation.append({
            "segment_index": 0,
            "segment_type": "ERROR",
            "english_text": "This HL7 message contains healthcare information but could not be fully translated into English.",
            "hyperlinks": []
        })
    
    return english_translation

@router.post("/parse", response_model=ParseMessageResponse)
async def parse_message(
    request: ParseMessageRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Parse an HL7 message and return comprehensive analysis using enhanced parser"""
    try:
        # Initialize enhanced HL7 parser
        parser = HL7Parser()
        
        # Parse the message using our enhanced parser
        parsed_message = parser.parse_message(request.raw_message)
        
        # Validate the message
        validation_errors = parser.validate_message(parsed_message)
        
        # Convert segments to API response format
        segments = []
        for segment in parsed_message.segments:
            segment_dict = {
                "segment_type": segment.type,
                "sequence": segment.sequence,
                "fields": [
                    {
                        "path": field.path,
                        "value": field.value,
                        "data_type": field.data_type,
                        "description": field.description,
                        "is_required": field.is_required,
                        "max_length": field.max_length
                    }
                    for field in segment.fields
                ],
                "is_valid": segment.is_valid,
                "validation_errors": segment.validation_errors or []
            }
            segments.append(segment_dict)
        
        return ParseMessageResponse(
            parsed=True,
            message_type=parsed_message.message_type,
            event_type=parsed_message.event_type,
            hl7_version=parsed_message.hl7_version,
            sending_application=parsed_message.sending_application,
            receiving_application=parsed_message.receiving_application,
            message_control_id=parsed_message.message_control_id,
            segments=segments,
            validation_errors=validation_errors,
            english_translation=parsed_message.english_translation,
            segment_count=len(parsed_message.segments)
        )
        
    except Exception as e:
        return ParseMessageResponse(
            parsed=False,
            message_type=None,
            event_type=None,
            hl7_version=None,
            sending_application=None,
            receiving_application=None,
            message_control_id=None,
            segments=[],
            validation_errors=[f"Failed to parse HL7 message: {str(e)}"],
            english_translation=[],
            segment_count=0
        )