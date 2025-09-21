import hl7
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass

@dataclass
class HL7Field:
    path: str
    value: str
    data_type: str
    description: str
    is_required: bool = False
    max_length: Optional[int] = None

@dataclass
class HL7Segment:
    type: str
    sequence: int
    raw_content: str
    fields: List[HL7Field]
    is_valid: bool = True
    validation_errors: List[str] = None

@dataclass
class ParsedHL7Message:
    message_type: str
    event_type: str
    hl7_version: str
    message_control_id: str
    sending_application: str
    sending_facility: str
    receiving_application: str
    receiving_facility: str
    segments: List[HL7Segment]
    encoding_chars: str
    field_separator: str
    english_translation: List[str]
    validation_errors: List[str] = None

    def to_dict(self) -> dict:
        """Convert ParsedHL7Message to dictionary"""
        return {
            'message_type': self.message_type,
            'event_type': self.event_type,
            'hl7_version': self.hl7_version,
            'message_control_id': self.message_control_id,
            'sending_application': self.sending_application,
            'sending_facility': self.sending_facility,
            'receiving_application': self.receiving_application,
            'receiving_facility': self.receiving_facility,
            'segments': [
                {
                    'type': seg.type,
                    'sequence': seg.sequence,
                    'raw_content': seg.raw_content,
                    'fields': [
                        {
                            'path': field.path,
                            'value': field.value,
                            'data_type': field.data_type,
                            'description': field.description,
                            'is_required': field.is_required,
                            'max_length': field.max_length
                        } for field in seg.fields
                    ],
                    'is_valid': seg.is_valid,
                    'validation_errors': seg.validation_errors or []
                } for seg in self.segments
            ],
            'encoding_chars': self.encoding_chars,
            'field_separator': self.field_separator,
            'english_translation': self.english_translation,
            'validation_errors': self.validation_errors or []
        }

class HL7Parser:
    def __init__(self):
        self.field_separator = "|"
        self.component_separator = "^"
        self.repetition_separator = "~"
        self.escape_character = "\\"
        self.subcomponent_separator = "&"
        
        # HL7 Segment definitions
        self.segment_definitions = self._load_segment_definitions()
        
        # English translation templates
        self.translation_templates = self._load_translation_templates()

    def _load_segment_definitions(self) -> Dict[str, Dict]:
        """Load HL7 segment field definitions"""
        return {
            "MSH": {
                "name": "Message Header",
                "fields": {
                    1: {"name": "Field Separator", "type": "ST", "required": True, "max_length": 1},
                    2: {"name": "Encoding Characters", "type": "ST", "required": True, "max_length": 4},
                    3: {"name": "Sending Application", "type": "HD", "required": False, "max_length": 227},
                    4: {"name": "Sending Facility", "type": "HD", "required": False, "max_length": 227},
                    5: {"name": "Receiving Application", "type": "HD", "required": False, "max_length": 227},
                    6: {"name": "Receiving Facility", "type": "HD", "required": False, "max_length": 227},
                    7: {"name": "Date/Time of Message", "type": "TS", "required": False, "max_length": 26},
                    8: {"name": "Security", "type": "ST", "required": False, "max_length": 40},
                    9: {"name": "Message Type", "type": "MSG", "required": True, "max_length": 15},
                    10: {"name": "Message Control ID", "type": "ST", "required": True, "max_length": 20},
                    11: {"name": "Processing ID", "type": "PT", "required": True, "max_length": 3},
                    12: {"name": "Version ID", "type": "VID", "required": True, "max_length": 60}
                }
            },
            "EVN": {
                "name": "Event Type",
                "fields": {
                    1: {"name": "Event Type Code", "type": "ID", "required": False, "max_length": 3},
                    2: {"name": "Recorded Date/Time", "type": "TS", "required": True, "max_length": 26},
                    3: {"name": "Date/Time Planned Event", "type": "TS", "required": False, "max_length": 26},
                    4: {"name": "Event Reason Code", "type": "IS", "required": False, "max_length": 3},
                    5: {"name": "Operator ID", "type": "XCN", "required": False, "max_length": 250},
                    6: {"name": "Event Occurred", "type": "TS", "required": False, "max_length": 26}
                }
            },
            "PID": {
                "name": "Patient Identification",
                "fields": {
                    1: {"name": "Set ID - PID", "type": "SI", "required": False, "max_length": 4},
                    2: {"name": "Patient ID", "type": "CX", "required": False, "max_length": 20},
                    3: {"name": "Patient Identifier List", "type": "CX", "required": True, "max_length": 250},
                    4: {"name": "Alternate Patient ID - PID", "type": "CX", "required": False, "max_length": 20},
                    5: {"name": "Patient Name", "type": "XPN", "required": True, "max_length": 250},
                    6: {"name": "Mother's Maiden Name", "type": "XPN", "required": False, "max_length": 250},
                    7: {"name": "Date/Time of Birth", "type": "TS", "required": False, "max_length": 26},
                    8: {"name": "Administrative Sex", "type": "IS", "required": False, "max_length": 1},
                    9: {"name": "Patient Alias", "type": "XPN", "required": False, "max_length": 250},
                    10: {"name": "Race", "type": "CE", "required": False, "max_length": 250},
                    11: {"name": "Patient Address", "type": "XAD", "required": False, "max_length": 250},
                    12: {"name": "County Code", "type": "IS", "required": False, "max_length": 4},
                    13: {"name": "Phone Number - Home", "type": "XTN", "required": False, "max_length": 250},
                    14: {"name": "Phone Number - Business", "type": "XTN", "required": False, "max_length": 250},
                    15: {"name": "Primary Language", "type": "CE", "required": False, "max_length": 250},
                    16: {"name": "Marital Status", "type": "CE", "required": False, "max_length": 250},
                    17: {"name": "Religion", "type": "CE", "required": False, "max_length": 250},
                    18: {"name": "Patient Account Number", "type": "CX", "required": False, "max_length": 20},
                    19: {"name": "SSN Number - Patient", "type": "ST", "required": False, "max_length": 16}
                }
            },
            "NK1": {
                "name": "Next of Kin / Associated Parties",
                "fields": {
                    1: {"name": "Set ID - NK1", "type": "SI", "required": True, "max_length": 4},
                    2: {"name": "Name", "type": "XPN", "required": False, "max_length": 250},
                    3: {"name": "Relationship", "type": "CE", "required": False, "max_length": 250},
                    4: {"name": "Address", "type": "XAD", "required": False, "max_length": 250},
                    5: {"name": "Phone Number", "type": "XTN", "required": False, "max_length": 250}
                }
            },
            "PV1": {
                "name": "Patient Visit",
                "fields": {
                    1: {"name": "Set ID - PV1", "type": "SI", "required": False, "max_length": 4},
                    2: {"name": "Patient Class", "type": "IS", "required": True, "max_length": 1},
                    3: {"name": "Assigned Patient Location", "type": "PL", "required": False, "max_length": 80},
                    4: {"name": "Admission Type", "type": "IS", "required": False, "max_length": 2},
                    5: {"name": "Preadmit Number", "type": "CX", "required": False, "max_length": 20},
                    6: {"name": "Prior Patient Location", "type": "PL", "required": False, "max_length": 80},
                    7: {"name": "Attending Doctor", "type": "XCN", "required": False, "max_length": 250},
                    8: {"name": "Referring Doctor", "type": "XCN", "required": False, "max_length": 250},
                    9: {"name": "Consulting Doctor", "type": "XCN", "required": False, "max_length": 250},
                    10: {"name": "Hospital Service", "type": "IS", "required": False, "max_length": 3},
                    17: {"name": "Admitting Doctor", "type": "XCN", "required": False, "max_length": 250},
                    18: {"name": "Patient Type", "type": "IS", "required": False, "max_length": 2},
                    19: {"name": "Visit Number", "type": "CX", "required": False, "max_length": 20}
                }
            },
            "ORC": {
                "name": "Common Order",
                "fields": {
                    1: {"name": "Order Control", "type": "ID", "required": True, "max_length": 2},
                    2: {"name": "Placer Order Number", "type": "EI", "required": False, "max_length": 22},
                    3: {"name": "Filler Order Number", "type": "EI", "required": False, "max_length": 22},
                    4: {"name": "Placer Group Number", "type": "EI", "required": False, "max_length": 22},
                    5: {"name": "Order Status", "type": "ID", "required": False, "max_length": 2},
                    6: {"name": "Response Flag", "type": "ID", "required": False, "max_length": 1},
                    9: {"name": "Date/Time of Transaction", "type": "TS", "required": False, "max_length": 26},
                    12: {"name": "Ordering Provider", "type": "XCN", "required": False, "max_length": 250}
                }
            },
            "OBR": {
                "name": "Observation Request",
                "fields": {
                    1: {"name": "Set ID - OBR", "type": "SI", "required": False, "max_length": 4},
                    2: {"name": "Placer Order Number", "type": "EI", "required": False, "max_length": 22},
                    3: {"name": "Filler Order Number", "type": "EI", "required": False, "max_length": 22},
                    4: {"name": "Universal Service Identifier", "type": "CE", "required": True, "max_length": 250},
                    6: {"name": "Requested Date/Time", "type": "TS", "required": False, "max_length": 26},
                    7: {"name": "Observation Date/Time", "type": "TS", "required": False, "max_length": 26},
                    15: {"name": "Specimen Source", "type": "SPS", "required": False, "max_length": 300},
                    16: {"name": "Ordering Provider", "type": "XCN", "required": False, "max_length": 250},
                    22: {"name": "Results Rpt/Status Chng - Date/Time", "type": "TS", "required": False, "max_length": 26},
                    25: {"name": "Result Status", "type": "ID", "required": False, "max_length": 1}
                }
            },
            "OBX": {
                "name": "Observation/Result",
                "fields": {
                    1: {"name": "Set ID - OBX", "type": "SI", "required": False, "max_length": 4},
                    2: {"name": "Value Type", "type": "ID", "required": False, "max_length": 3},
                    3: {"name": "Observation Identifier", "type": "CE", "required": True, "max_length": 250},
                    4: {"name": "Observation Sub-ID", "type": "ST", "required": False, "max_length": 20},
                    5: {"name": "Observation Value", "type": "Varies", "required": False, "max_length": 99999},
                    6: {"name": "Units", "type": "CE", "required": False, "max_length": 250},
                    7: {"name": "References Range", "type": "ST", "required": False, "max_length": 60},
                    8: {"name": "Abnormal Flags", "type": "IS", "required": False, "max_length": 5},
                    11: {"name": "Result Status", "type": "ID", "required": True, "max_length": 1},
                    14: {"name": "Date/Time of the Observation", "type": "TS", "required": False, "max_length": 26}
                }
            }
        }

    def _load_translation_templates(self) -> Dict[str, Dict]:
        """Load English translation templates"""
        return {
            "ADT^A01": {
                "title": "Admit/Visit Notification",
                "templates": [
                    "This is an {message_type} message.",
                    "Patient {patient_name} (MRN: {patient_id}) was admitted on {event_time}.",
                    "Patient is a {age}-year-old {gender}, born {birth_date}.",
                    "Patient address: {patient_address}",
                    "Emergency contact: {nok_name} ({nok_relationship}) at {nok_address}",
                    "Admitted to {patient_location}",
                    "Attending physician: {attending_doctor}",
                    "Service type: {hospital_service}"
                ]
            },
            "ADT^A04": {
                "title": "Register a Patient",
                "templates": [
                    "This is a patient registration ({message_type}) message.",
                    "Patient {patient_name} (MRN: {patient_id}) was registered on {event_time}.",
                    "Patient demographics: {age}-year-old {gender}, born {birth_date}.",
                    "Contact information: {patient_address}, {phone_home}"
                ]
            },
            "ORM^O01": {
                "title": "Order Message",
                "templates": [
                    "This is an Order Message ({message_type}) for {test_name}.",
                    "Order placed for patient {patient_name} (MRN: {patient_id})",
                    "Test ordered: {test_name}",
                    "Ordered by {ordering_provider}",
                    "Order date/time: {order_time}",
                    "Sample type: {specimen_source}",
                    "Status: {order_status}"
                ]
            },
            "ORU^R01": {
                "title": "Observation Result",
                "templates": [
                    "This is a Result Message ({message_type}) containing {test_name} results.",
                    "Results for patient {patient_name} (MRN: {patient_id})",
                    "Test: {test_name} completed on {result_time}",
                    "{observation_results}",
                    "{result_interpretation}"
                ]
            }
        }

    def parse_message(self, raw_message: str) -> ParsedHL7Message:
        """Parse an HL7 message string into structured data using proper HL7 library"""
        if not raw_message or not raw_message.strip():
            raise ValueError("Message text cannot be empty")
        
        try:
            # Parse using the hl7 library
            message_text = raw_message.strip()
            # Replace line endings consistently
            message_text = message_text.replace('\r\n', '\r').replace('\n', '\r')
            
            # Parse with hl7 library
            msg = hl7.parse(message_text)
            
            # Extract MSH segment for basic info
            msh = msg.segment('MSH')
            if not msh:
                raise ValueError("No MSH segment found in message")
            
            # Extract message components with proper field indexing (HL7 uses 1-based indexing)
            message_type = str(msh[9]) if len(msh) > 9 else ""  # MSH.9 (Message Type)
            message_control_id = str(msh[10]) if len(msh) > 10 else ""  # MSH.10 (Message Control ID)  
            processing_id = str(msh[11]) if len(msh) > 11 else ""  # MSH.11 (Processing ID)
            version_id = str(msh[12]) if len(msh) > 12 else ""  # MSH.12 (Version ID)
            
            # Parse message type components
            if '^' in message_type:
                msg_type_parts = message_type.split('^')
                message_type_code = msg_type_parts[0]
                event_type = msg_type_parts[1] if len(msg_type_parts) > 1 else ""
            else:
                message_type_code = message_type
                event_type = ""
            
            # Extract field separator and encoding characters
            field_separator = str(msh[1]) if len(msh) > 1 else "|"
            encoding_chars = str(msh[2]) if len(msh) > 2 else "^~\\&"
            
            # Update instance variables for compatibility
            self.field_separator = field_separator
            if len(encoding_chars) >= 4:
                self.component_separator = encoding_chars[0]
                self.repetition_separator = encoding_chars[1] 
                self.escape_character = encoding_chars[2]
                self.subcomponent_separator = encoding_chars[3]
            
            # Extract application and facility info
            sending_application = str(msh[3]) if len(msh) > 3 else ""
            sending_facility = str(msh[4]) if len(msh) > 4 else ""
            receiving_application = str(msh[5]) if len(msh) > 5 else ""
            receiving_facility = str(msh[6]) if len(msh) > 6 else ""
            
            # Parse all segments using existing structure
            segments = []
            for i, segment in enumerate(msg):
                segment_type = str(segment[0]) if len(segment) > 0 else "UNK"
                
                # Convert segment to raw string for existing _parse_segment method
                segment_fields = []
                for j in range(len(segment)):
                    field_value = str(segment[j]) if segment[j] is not None else ""
                    segment_fields.append(field_value)
                
                # Reconstruct segment line for compatibility with existing parsing
                if segment_type == "MSH":
                    segment_line = segment_type + field_separator + field_separator.join(segment_fields[1:])
                else:
                    segment_line = segment_type + field_separator + field_separator.join(segment_fields[1:])
                
                # Use existing segment parsing method
                parsed_segment = self._parse_segment(segment_line, i)
                segments.append(parsed_segment)
            
            # Generate comprehensive English translation
            english_translation = self._generate_comprehensive_english_translation(
                msg, message_type_code, event_type
            )
            
            return ParsedHL7Message(
                message_type=message_type,
                event_type=event_type,
                hl7_version=version_id,
                message_control_id=message_control_id,
                sending_application=sending_application,
                sending_facility=sending_facility,
                receiving_application=receiving_application,
                receiving_facility=receiving_facility,
                segments=segments,
                encoding_chars=encoding_chars,
                field_separator=field_separator,
                english_translation=english_translation,
                validation_errors=[]
            )
            
        except Exception as e:
            raise ValueError(f"Failed to parse HL7 message: {str(e)}")

    def _generate_comprehensive_english_translation(self, msg, message_type_code: str, event_type: str) -> List[str]:
        """Generate comprehensive English translation for HL7 messages"""
        translation = []
        
        try:
            # Get MSH segment
            msh = msg.segment('MSH')
            if msh:
                sending_app = str(msh[3]) if len(msh) > 3 else "Unknown Application"
                receiving_app = str(msh[5]) if len(msh) > 5 else "Unknown Application"
                msg_datetime = str(msh[7]) if len(msh) > 7 else ""
                
                # Format timestamp
                formatted_time = "at an unknown time"
                if msg_datetime:
                    try:
                        if len(msg_datetime) >= 8:
                            dt = datetime.strptime(msg_datetime[:14], "%Y%m%d%H%M%S")
                            formatted_time = f"on {dt.strftime('%B %d, %Y at %I:%M %p')}"
                    except:
                        formatted_time = f"at {msg_datetime}"
                
                # Message type specific translations
                if message_type_code == "ADT":
                    event_descriptions = {
                        "A01": "patient admission",
                        "A02": "patient transfer", 
                        "A03": "patient discharge",
                        "A04": "patient registration",
                        "A05": "patient pre-admission",
                        "A08": "patient information update",
                        "A11": "patient cancellation",
                        "A12": "patient cancellation of transfer",
                        "A13": "patient cancellation of discharge"
                    }
                    event_desc = event_descriptions.get(event_type, f"patient event ({event_type})")
                    translation.append(f"This is an {event_desc} message sent from {sending_app} to {receiving_app} {formatted_time}.")
                
                elif message_type_code == "SIU":
                    event_descriptions = {
                        "S12": "notification of new appointment booking",
                        "S13": "notification of appointment rescheduling", 
                        "S14": "notification of appointment modification",
                        "S15": "notification of appointment cancellation",
                        "S17": "notification of appointment deletion"
                    }
                    event_desc = event_descriptions.get(event_type, f"scheduling event ({event_type})")
                    translation.append(f"This is a {event_desc} sent from {sending_app} to {receiving_app} {formatted_time}.")
                
                elif message_type_code == "ORU":
                    translation.append(f"This is an observation result message sent from {sending_app} to {receiving_app} {formatted_time}.")
                
                elif message_type_code == "ORM":
                    translation.append(f"This is an order message sent from {sending_app} to {receiving_app} {formatted_time}.")
                
                else:
                    translation.append(f"This is a {message_type_code}^{event_type} message sent from {sending_app} to {receiving_app} {formatted_time}.")
            
            # Parse specific segments for detailed information
            
            # Patient Information (PID)
            pid = msg.segment('PID')
            if pid:
                patient_name = str(pid[5]) if len(pid) > 5 else ""
                patient_id = str(pid[3]) if len(pid) > 3 else ""
                dob = str(pid[7]) if len(pid) > 7 else ""
                gender = str(pid[8]) if len(pid) > 8 else ""
                
                if patient_name:
                    # Parse name components (Last^First^Middle)
                    name_parts = patient_name.split('^')
                    if len(name_parts) >= 2:
                        formatted_name = f"{name_parts[1]} {name_parts[0]}"
                        if len(name_parts) > 2 and name_parts[2]:
                            formatted_name = f"{name_parts[1]} {name_parts[2]} {name_parts[0]}"
                    else:
                        formatted_name = patient_name
                    
                    patient_info = f"The patient is {formatted_name}"
                    if patient_id:
                        patient_info += f" (ID: {patient_id})"
                    if dob:
                        try:
                            if len(dob) >= 8:
                                birth_date = datetime.strptime(dob[:8], "%Y%m%d")
                                patient_info += f", born on {birth_date.strftime('%B %d, %Y')}"
                        except:
                            patient_info += f", born on {dob}"
                    if gender:
                        gender_text = {"M": "Male", "F": "Female", "O": "Other"}.get(gender, gender)
                        patient_info += f", {gender_text}"
                    
                    translation.append(patient_info + ".")
            
            # Visit Information (PV1)
            try:
                pv1 = msg.segment('PV1')
            except:
                pv1 = None
            if pv1:
                patient_class = str(pv1[2]) if len(pv1) > 2 else ""
                location = str(pv1[3]) if len(pv1) > 3 else ""
                attending_doctor = str(pv1[7]) if len(pv1) > 7 else ""
                
                visit_info = "Visit details: "
                if patient_class:
                    class_desc = {"I": "Inpatient", "O": "Outpatient", "E": "Emergency", "P": "Preadmit"}.get(patient_class, patient_class)
                    visit_info += f"{class_desc} visit"
                if location:
                    visit_info += f" at location {location}"
                if attending_doctor:
                    # Parse doctor name
                    doc_parts = attending_doctor.split('^')
                    if len(doc_parts) >= 2:
                        doc_name = f"Dr. {doc_parts[1]} {doc_parts[0]}"
                    else:
                        doc_name = f"Dr. {attending_doctor}"
                    visit_info += f" with {doc_name}"
                
                if visit_info != "Visit details: ":
                    translation.append(visit_info + ".")
            
            # Scheduling Information (SCH)
            try:
                sch = msg.segment('SCH')
            except:
                sch = None
            if sch:
                appointment_id = str(sch[1]) if len(sch) > 1 else ""
                event_reason = str(sch[7]) if len(sch) > 7 else ""
                appointment_type = str(sch[8]) if len(sch) > 8 else ""
                duration = str(sch[9]) if len(sch) > 9 else ""
                start_time = str(sch[11]) if len(sch) > 11 else ""
                
                appt_info = "Appointment details: "
                if appointment_type:
                    appt_info += f"{appointment_type} appointment"
                if appointment_id:
                    appt_info += f" (ID: {appointment_id})"
                if duration:
                    appt_info += f" scheduled for {duration} minutes"
                if start_time:
                    try:
                        if len(start_time) >= 14:
                            appt_time = datetime.strptime(start_time[:14], "%Y%m%d%H%M%S")
                            appt_info += f" on {appt_time.strftime('%B %d, %Y at %I:%M %p')}"
                    except:
                        appt_info += f" at {start_time}"
                if event_reason:
                    appt_info += f" for {event_reason}"
                
                if appt_info != "Appointment details: ":
                    translation.append(appt_info + ".")
            
            # Observation Results (OBX)
            obx_segments = [seg for seg in msg if str(seg[0]) == 'OBX']
            if obx_segments:
                translation.append("Laboratory/Observation results:")
                for obx in obx_segments[:5]:  # Limit to first 5 results
                    observation_id = str(obx[3]) if len(obx) > 3 else ""
                    value = str(obx[5]) if len(obx) > 5 else ""
                    units = str(obx[6]) if len(obx) > 6 else ""
                    
                    if observation_id and value:
                        obs_name = observation_id.split('^')[1] if '^' in observation_id else observation_id
                        result_text = f"- {obs_name}: {value}"
                        if units:
                            result_text += f" {units}"
                        translation.append(result_text)
            
            if not translation:
                translation.append("This is an HL7 message with standard healthcare information.")
                
        except Exception as e:
            translation = [f"This is an HL7 {message_type_code}^{event_type} message with healthcare information."]
        
        return translation

    def _parse_segment(self, segment_line: str, sequence: int) -> HL7Segment:
        """Parse a single HL7 segment"""
        if len(segment_line) < 3:
            raise ValueError(f"Invalid segment line: {segment_line}")

        segment_type = segment_line[:3]
        fields = []

        # Handle MSH segment specially (field separator is different)
        if segment_type == "MSH":
            # MSH field 1 is the field separator itself
            fields.append(HL7Field("MSH.1", self.field_separator, "ST", "Field Separator", True))
            # MSH field 2 is encoding characters
            fields.append(HL7Field("MSH.2", segment_line[4:8], "ST", "Encoding Characters", True))
            # Remaining fields start from position 8
            remaining_fields = segment_line[8:].split(self.field_separator) if len(segment_line) > 8 else []
            field_start = 3
        else:
            remaining_fields = segment_line[4:].split(self.field_separator) if len(segment_line) > 4 else []
            field_start = 1

        # Parse remaining fields
        segment_def = self.segment_definitions.get(segment_type, {"fields": {}})
        for i, field_value in enumerate(remaining_fields):
            field_num = field_start + i
            field_def = segment_def["fields"].get(field_num, {
                "name": f"Field {field_num}",
                "type": "ST",
                "required": False
            })
            
            field_path = f"{segment_type}.{field_num}"
            fields.append(HL7Field(
                path=field_path,
                value=field_value,
                data_type=field_def.get("type", "ST"),
                description=field_def.get("name", f"Field {field_num}"),
                is_required=field_def.get("required", False),
                max_length=field_def.get("max_length")
            ))

        return HL7Segment(
            type=segment_type,
            sequence=sequence,
            raw_content=segment_line,
            fields=fields,
            is_valid=True,
            validation_errors=[]
        )

    def _get_field_value(self, segment: HL7Segment, field_path: str, default: str = "") -> str:
        """Get field value by path"""
        field = next((f for f in segment.fields if f.path == field_path), None)
        return field.value if field else default

    def _generate_english_translation(self, message_type: str, segments: List[HL7Segment]) -> List[str]:
        """Generate human-readable English translation"""
        try:
            template = self.translation_templates.get(message_type)
            if not template:
                return [f"This is a {message_type} message."]

            # Extract common data elements
            data = self._extract_message_data(segments)
            
            # Generate translation from templates
            translations = []
            translations.append(template["title"] + f" ({message_type}) message.")
            
            for template_str in template.get("templates", []):
                try:
                    translated = template_str.format(**data)
                    if translated and not translated.startswith("This is a {"):
                        translations.append(translated)
                except (KeyError, ValueError):
                    # Skip templates with missing data
                    continue
            
            return translations if translations else [f"This is a {message_type} message."]
            
        except Exception:
            return [f"This is a {message_type} message."]

    def _extract_message_data(self, segments: List[HL7Segment]) -> Dict[str, str]:
        """Extract key data elements from segments for translation"""
        data = {}
        
        # Find segments by type
        msh = next((s for s in segments if s.type == "MSH"), None)
        evn = next((s for s in segments if s.type == "EVN"), None)
        pid = next((s for s in segments if s.type == "PID"), None)
        pv1 = next((s for s in segments if s.type == "PV1"), None)
        nk1 = next((s for s in segments if s.type == "NK1"), None)
        orc = next((s for s in segments if s.type == "ORC"), None)
        obr = next((s for s in segments if s.type == "OBR"), None)
        obx_segments = [s for s in segments if s.type == "OBX"]

        # Extract MSH data
        if msh:
            data["message_type"] = self._get_field_value(msh, "MSH.9", "")
            data["sending_application"] = self._get_field_value(msh, "MSH.3", "")

        # Extract EVN data
        if evn:
            event_time = self._get_field_value(evn, "EVN.2", "")
            data["event_time"] = self._format_hl7_datetime(event_time)

        # Extract PID data
        if pid:
            patient_name = self._get_field_value(pid, "PID.5", "")
            data["patient_name"] = self._format_patient_name(patient_name)
            data["patient_id"] = self._get_field_value(pid, "PID.3", "").split(self.component_separator)[0]
            
            birth_date = self._get_field_value(pid, "PID.7", "")
            data["birth_date"] = self._format_hl7_date(birth_date)
            data["age"] = self._calculate_age(birth_date)
            
            gender_code = self._get_field_value(pid, "PID.8", "")
            data["gender"] = self._format_gender(gender_code)
            
            address = self._get_field_value(pid, "PID.11", "")
            data["patient_address"] = self._format_address(address)
            
            data["phone_home"] = self._get_field_value(pid, "PID.13", "")

        # Extract PV1 data
        if pv1:
            location = self._get_field_value(pv1, "PV1.3", "")
            data["patient_location"] = self._format_location(location)
            
            attending = self._get_field_value(pv1, "PV1.7", "")
            data["attending_doctor"] = self._format_provider_name(attending)
            
            data["hospital_service"] = self._get_field_value(pv1, "PV1.10", "")

        # Extract NK1 data
        if nk1:
            nok_name = self._get_field_value(nk1, "NK1.2", "")
            data["nok_name"] = self._format_patient_name(nok_name)
            
            relationship = self._get_field_value(nk1, "NK1.3", "")
            data["nok_relationship"] = self._format_relationship(relationship)
            
            nok_address = self._get_field_value(nk1, "NK1.4", "")
            data["nok_address"] = self._format_address(nok_address)

        # Extract order data
        if obr:
            test_name = self._get_field_value(obr, "OBR.4", "")
            data["test_name"] = self._format_test_name(test_name)
            
            ordering_provider = self._get_field_value(obr, "OBR.16", "")
            data["ordering_provider"] = self._format_provider_name(ordering_provider)
            
            order_time = self._get_field_value(obr, "OBR.6", "")
            data["order_time"] = self._format_hl7_datetime(order_time)
            
            result_time = self._get_field_value(obr, "OBR.7", "")
            data["result_time"] = self._format_hl7_datetime(result_time)
            
            specimen = self._get_field_value(obr, "OBR.15", "")
            data["specimen_source"] = self._format_specimen(specimen)

        if orc:
            data["order_status"] = self._format_order_status(self._get_field_value(orc, "ORC.1", ""))

        # Extract observation results
        if obx_segments:
            observations = []
            for obx in obx_segments:
                test_name = self._get_field_value(obx, "OBX.3", "")
                value = self._get_field_value(obx, "OBX.5", "")
                units = self._get_field_value(obx, "OBX.6", "")
                reference_range = self._get_field_value(obx, "OBX.7", "")
                
                formatted_test = self._format_test_name(test_name)
                formatted_value = f"{value} {units}".strip() if units else value
                
                if reference_range:
                    observations.append(f"{formatted_test}: {formatted_value} (Normal range: {reference_range})")
                else:
                    observations.append(f"{formatted_test}: {formatted_value}")
            
            data["observation_results"] = "\n".join(observations)
            
            # Determine overall interpretation
            abnormal_flags = [self._get_field_value(obx, "OBX.8", "") for obx in obx_segments]
            if any(flag and flag.upper() in ["H", "L", "A", "AA"] for flag in abnormal_flags):
                data["result_interpretation"] = "Some results are outside normal limits"
            else:
                data["result_interpretation"] = "All results are within normal limits"

        return data

    def _format_patient_name(self, name_field: str) -> str:
        """Format HL7 patient name field"""
        if not name_field:
            return "Unknown"
        
        # Split by component separator (^)
        parts = name_field.split(self.component_separator)
        last_name = parts[0] if len(parts) > 0 else ""
        first_name = parts[1] if len(parts) > 1 else ""
        middle_name = parts[2] if len(parts) > 2 else ""
        
        name_parts = [first_name, middle_name, last_name]
        return " ".join(part for part in name_parts if part).strip() or "Unknown"

    def _format_provider_name(self, provider_field: str) -> str:
        """Format HL7 provider name field"""
        if not provider_field:
            return "Unknown Provider"
        
        parts = provider_field.split(self.component_separator)
        if len(parts) >= 2:
            last_name = parts[1] if parts[1] else ""
            first_name = parts[2] if len(parts) > 2 and parts[2] else ""
            prefix = parts[5] if len(parts) > 5 and parts[5] else "Dr."
            
            if first_name and last_name:
                return f"{prefix} {first_name} {last_name}"
            elif last_name:
                return f"{prefix} {last_name}"
        
        return provider_field.split(self.component_separator)[0] or "Unknown Provider"

    def _format_address(self, address_field: str) -> str:
        """Format HL7 address field"""
        if not address_field:
            return ""
        
        parts = address_field.split(self.component_separator)
        street = parts[0] if len(parts) > 0 else ""
        city = parts[2] if len(parts) > 2 else ""
        state = parts[3] if len(parts) > 3 else ""
        zip_code = parts[4] if len(parts) > 4 else ""
        
        address_parts = [street, city, state, zip_code]
        return ", ".join(part for part in address_parts if part).strip()

    def _format_location(self, location_field: str) -> str:
        """Format HL7 location field"""
        if not location_field:
            return "Unknown location"
        
        parts = location_field.split(self.component_separator)
        room = parts[0] if len(parts) > 0 else ""
        bed = parts[1] if len(parts) > 1 else ""
        unit = parts[2] if len(parts) > 2 else ""
        
        location_parts = []
        if room:
            location_parts.append(f"Room {room}")
        if bed:
            location_parts.append(f"Bed {bed}")
        if unit:
            location_parts.append(f"Unit {unit}")
        
        return ", ".join(location_parts) if location_parts else location_field

    def _format_test_name(self, test_field: str) -> str:
        """Format HL7 test name field"""
        if not test_field:
            return "Unknown test"
        
        parts = test_field.split(self.component_separator)
        return parts[1] if len(parts) > 1 and parts[1] else parts[0]

    def _format_specimen(self, specimen_field: str) -> str:
        """Format HL7 specimen field"""
        if not specimen_field:
            return "Unknown specimen"
        
        parts = specimen_field.split(self.component_separator)
        return parts[1] if len(parts) > 1 and parts[1] else parts[0]

    def _format_gender(self, gender_code: str) -> str:
        """Format gender code"""
        gender_map = {"M": "Male", "F": "Female", "O": "Other", "U": "Unknown"}
        return gender_map.get(gender_code, gender_code or "Unknown")

    def _format_relationship(self, relationship_field: str) -> str:
        """Format relationship field"""
        if not relationship_field:
            return "Contact"
        
        parts = relationship_field.split(self.component_separator)
        relationship_code = parts[0] if parts else ""
        
        relationship_map = {
            "SPO": "Spouse",
            "CHD": "Child", 
            "PAR": "Parent",
            "SIB": "Sibling",
            "EMC": "Emergency Contact"
        }
        
        return relationship_map.get(relationship_code, relationship_code or "Contact")

    def _format_order_status(self, status_code: str) -> str:
        """Format order status"""
        status_map = {
            "NW": "New order",
            "OK": "Order accepted",
            "CA": "Order cancelled",
            "DC": "Order discontinued",
            "CM": "Order completed"
        }
        return status_map.get(status_code, status_code or "Unknown status")

    def _format_hl7_datetime(self, datetime_str: str) -> str:
        """Format HL7 datetime string"""
        if not datetime_str or len(datetime_str) < 8:
            return datetime_str
        
        try:
            # HL7 format: YYYYMMDDHHMMSS
            year = datetime_str[:4]
            month = datetime_str[4:6]
            day = datetime_str[6:8]
            
            if len(datetime_str) >= 10:
                hour = datetime_str[8:10]
                minute = datetime_str[10:12] if len(datetime_str) >= 12 else "00"
                return f"{month}/{day}/{year} at {hour}:{minute}"
            else:
                return f"{month}/{day}/{year}"
        except:
            return datetime_str

    def _format_hl7_date(self, date_str: str) -> str:
        """Format HL7 date string"""
        if not date_str or len(date_str) < 8:
            return date_str
        
        try:
            # HL7 format: YYYYMMDD
            year = date_str[:4]
            month = date_str[4:6]
            day = date_str[6:8]
            return f"{month}/{day}/{year}"
        except:
            return date_str

    def _calculate_age(self, birth_date_str: str) -> str:
        """Calculate age from HL7 birth date"""
        if not birth_date_str or len(birth_date_str) < 8:
            return "Unknown"
        
        try:
            birth_year = int(birth_date_str[:4])
            current_year = datetime.now().year
            age = current_year - birth_year
            return str(age)
        except:
            return "Unknown"

    def validate_message(self, parsed_message: ParsedHL7Message) -> List[str]:
        """Validate parsed HL7 message using proper HL7 standards"""
        errors = []
        
        try:
            # Basic validation
            if not parsed_message.message_type:
                errors.append("Message type is missing")
            
            if not parsed_message.message_control_id:
                errors.append("Message control ID is missing")
            
            # Validate required segments based on message type
            segment_types = [s.type for s in parsed_message.segments]
            
            if "MSH" not in segment_types:
                errors.append("Missing required MSH segment")
            
            # Message type specific validation
            message_code = parsed_message.message_type.split('^')[0] if '^' in parsed_message.message_type else parsed_message.message_type
            
            if message_code == "ADT":
                if "PID" not in segment_types:
                    errors.append("ADT messages require PID (Patient Identification) segment")
                    
            elif message_code == "SIU":
                if "SCH" not in segment_types:
                    errors.append("SIU messages require SCH (Schedule Activity Information) segment")
                if "PID" not in segment_types:
                    errors.append("SIU messages require PID (Patient Identification) segment")
                    
            elif message_code == "ORM":
                if "ORC" not in segment_types:
                    errors.append("ORM messages require ORC (Common Order) segment")
                if "OBR" not in segment_types:
                    errors.append("ORM messages require OBR (Observation Request) segment")
                    
            elif message_code == "ORU":
                if "OBR" not in segment_types:
                    errors.append("ORU messages require OBR (Observation Request) segment")
                if "OBX" not in segment_types:
                    errors.append("ORU messages require OBX (Observation/Result) segment")
            
            # Basic field validation for MSH segment
            msh_segment = None
            for segment in parsed_message.segments:
                if segment.type == "MSH":
                    msh_segment = segment
                    break
            
            if msh_segment and len(msh_segment.fields) > 0:
                # Check key MSH fields
                if len(msh_segment.fields) < 12:
                    errors.append("MSH segment is missing required fields")
                else:
                    # Find the correct field positions (MSH fields are numbered differently)
                    msg_type_field = None
                    control_id_field = None
                    processing_id_field = None
                    
                    for field in msh_segment.fields:
                        if field.path == "MSH.9":
                            msg_type_field = field
                        elif field.path == "MSH.10":
                            control_id_field = field
                        elif field.path == "MSH.11":
                            processing_id_field = field
                    
                    # MSH.9 (Message Type) should not be empty
                    if msg_type_field and not msg_type_field.value.strip():
                        errors.append("MSH.9 (Message Type) is required")
                    elif not msg_type_field:
                        errors.append("MSH.9 (Message Type) field is missing")
                    
                    # MSH.10 (Message Control ID) should not be empty
                    if control_id_field and not control_id_field.value.strip():
                        errors.append("MSH.10 (Message Control ID) is required")
                    elif not control_id_field:
                        errors.append("MSH.10 (Message Control ID) field is missing")
                    
                    # MSH.11 (Processing ID) should be P, T, or D
                    if processing_id_field:
                        processing_id = processing_id_field.value.strip()
                        if processing_id and processing_id not in ['P', 'T', 'D']:
                            errors.append("MSH.11 (Processing ID) should be P (Production), T (Training), or D (Debugging)")
                    else:
                        errors.append("MSH.11 (Processing ID) field is missing")
            
            # If no errors found, message is valid
            if not errors:
                return []
                
        except Exception as e:
            errors.append(f"Validation error: {str(e)}")
        
        return errors