"""
Generic HL7 Mapper Service
Supports all HL7 versions (2.x, 3.x, FHIR) with intelligent field mapping
"""

import re
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import json
from services.hl7_mapper_service import hl7_mapper_service

class HL7Version(Enum):
    """Supported HL7 versions"""
    V2_1 = "2.1"
    V2_2 = "2.2" 
    V2_3 = "2.3"
    V2_4 = "2.4"
    V2_5 = "2.5"
    V2_6 = "2.6"
    V2_7 = "2.7"
    V2_8 = "2.8"
    V3 = "3.0"
    FHIR_R4 = "FHIR_R4"
    UNKNOWN = "UNKNOWN"

@dataclass
class HL7FieldDefinition:
    """Definition of an HL7 field with version support"""
    segment: str
    field_number: int
    component: int = 0
    subcomponent: int = 0
    name: str = ""
    description: str = ""
    data_type: str = ""
    required: bool = False
    max_length: Optional[int] = None
    supported_versions: List[HL7Version] = None

@dataclass
class HL7ParsedField:
    """Parsed HL7 field with metadata"""
    value: str
    field_definition: HL7FieldDefinition
    raw_field: str
    is_valid: bool = True
    validation_errors: List[str] = None

class GenericHL7Mapper:
    """Generic HL7 mapper supporting all versions with intelligent field detection"""
    
    def __init__(self):
        self.field_definitions = self._load_field_definitions()
        self.common_field_mappings = self._load_common_field_mappings()
        
    def parse_message(self, hl7_message: str) -> Dict[str, Any]:
        """
        Parse HL7 message generically, detecting version and structure
        
        Args:
            hl7_message: Raw HL7 message string
            
        Returns:
            Dictionary with parsed message data
        """
        result = {
            "raw_message": hl7_message,
            "version": self.detect_hl7_version(hl7_message),
            "segments": {},
            "parsed_fields": {},
            "common_fields": {},
            "validation_errors": []
        }
        
        try:
            # Parse segments
            segments = self._parse_segments(hl7_message)
            result["segments"] = segments
            
            # Extract common fields across all versions
            common_fields = self._extract_common_fields(segments, result["version"])
            result["common_fields"] = common_fields
            
            # Parse specific fields based on version
            parsed_fields = self._parse_version_specific_fields(segments, result["version"])
            result["parsed_fields"] = parsed_fields
            
        except Exception as e:
            result["validation_errors"].append(f"Parsing error: {str(e)}")
            
        return result
    
    def detect_hl7_version(self, hl7_message: str) -> HL7Version:
        """
        Detect HL7 version from message header
        
        Args:
            hl7_message: Raw HL7 message
            
        Returns:
            Detected HL7Version
        """
        try:
            # Check for FHIR JSON structure
            if hl7_message.strip().startswith('{'):
                try:
                    fhir_data = json.loads(hl7_message)
                    if 'resourceType' in fhir_data:
                        return HL7Version.FHIR_R4
                except:
                    pass
            
            # Parse MSH segment for v2.x version
            lines = hl7_message.strip().split('\n')
            if lines and lines[0].startswith('MSH'):
                msh_segment = lines[0]
                fields = msh_segment.split('|')
                
                # Version is typically in MSH.12 (field 12)
                if len(fields) >= 13:  # 0-indexed, so field 12 is index 12
                    version_field = fields[12].strip()
                    
                    # Map version strings to enum
                    version_map = {
                        "2.1": HL7Version.V2_1,
                        "2.2": HL7Version.V2_2,
                        "2.3": HL7Version.V2_3,
                        "2.4": HL7Version.V2_4,
                        "2.5": HL7Version.V2_5,
                        "2.6": HL7Version.V2_6,
                        "2.7": HL7Version.V2_7,
                        "2.8": HL7Version.V2_8,
                    }
                    
                    return version_map.get(version_field, HL7Version.UNKNOWN)
            
        except Exception as e:
            print(f"Version detection error: {e}")
            
        return HL7Version.UNKNOWN
    
    def extract_field_generic(self, hl7_message: str, field_path: str, default: str = "") -> str:
        """
        Generic field extraction supporting multiple path formats and versions
        
        Supported path formats:
        - Standard: PID.5.1 (segment.field.component)
        - Extended: PID.5.1.2 (segment.field.component.subcomponent)
        - Named: Patient.Name.Last (using common field mappings)
        - Xpath-like: //PID[1]/field[5]/component[1]
        
        Args:
            hl7_message: Raw HL7 message
            field_path: Field path in various supported formats
            default: Default value if field not found
            
        Returns:
            Extracted field value or default
        """
        try:
            parsed_message = self.parse_message(hl7_message)
            
            # Handle named field paths (e.g., "Patient.Name.Last")
            if not field_path.startswith(('MSH', 'EVN', 'PID', 'ORC', 'OBR', 'OBX', 'NK1', 'PV1', 'AL1', 'DG1')):
                field_path = self._resolve_named_field_path(field_path, parsed_message["version"])
            
            # Handle XPath-like paths
            if field_path.startswith('//'):
                return self._extract_xpath_field(parsed_message["segments"], field_path, default)
            
            # Handle standard dot notation (e.g., PID.5.1)
            return self._extract_dot_notation_field(parsed_message["segments"], field_path, default)
            
        except Exception as e:
            print(f"Generic field extraction error for {field_path}: {e}")
            return default
    
    def _parse_segments(self, hl7_message: str) -> Dict[str, List[str]]:
        """Parse HL7 message into segments"""
        segments = {}
        lines = hl7_message.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Extract segment name (first 3 characters)
            segment_name = line[:3]
            
            if segment_name not in segments:
                segments[segment_name] = []
            
            segments[segment_name].append(line)
        
        return segments
    
    def _extract_common_fields(self, segments: Dict[str, List[str]], version: HL7Version) -> Dict[str, str]:
        """Extract commonly used fields across all HL7 versions"""
        common_fields = {}
        
        # Patient information (from PID segment)
        if 'PID' in segments and segments['PID']:
            pid_segment = segments['PID'][0]
            common_fields.update({
                'patient_id': self._extract_field_from_segment(pid_segment, 3, 0),
                'patient_last_name': self._extract_field_from_segment(pid_segment, 5, 0),
                'patient_first_name': self._extract_field_from_segment(pid_segment, 5, 1),
                'patient_middle_name': self._extract_field_from_segment(pid_segment, 5, 2),
                'patient_full_name': self._extract_field_from_segment(pid_segment, 5),
                'patient_dob': self._extract_field_from_segment(pid_segment, 7, 0),
                'patient_gender': self._extract_field_from_segment(pid_segment, 8, 0),
                'patient_address': self._extract_field_from_segment(pid_segment, 11),
                'patient_phone': self._extract_field_from_segment(pid_segment, 13, 0),
            })
        
        # Message header information (from MSH segment)  
        if 'MSH' in segments and segments['MSH']:
            msh_segment = segments['MSH'][0]
            common_fields.update({
                'sending_application': self._extract_field_from_segment(msh_segment, 3, 0),
                'sending_facility': self._extract_field_from_segment(msh_segment, 4, 0),
                'receiving_application': self._extract_field_from_segment(msh_segment, 5, 0),
                'receiving_facility': self._extract_field_from_segment(msh_segment, 6, 0),
                'message_datetime': self._extract_field_from_segment(msh_segment, 7, 0),
                'message_type': self._extract_field_from_segment(msh_segment, 9, 0),
                'message_event': self._extract_field_from_segment(msh_segment, 9, 1),
                'message_control_id': self._extract_field_from_segment(msh_segment, 10, 0),
                'processing_id': self._extract_field_from_segment(msh_segment, 11, 0),
                'hl7_version': self._extract_field_from_segment(msh_segment, 12, 0),
            })
        
        # Event information (from EVN segment)
        if 'EVN' in segments and segments['EVN']:
            evn_segment = segments['EVN'][0] 
            common_fields.update({
                'event_type': self._extract_field_from_segment(evn_segment, 1, 0),
                'event_datetime': self._extract_field_from_segment(evn_segment, 2, 0),
                'event_operator_id': self._extract_field_from_segment(evn_segment, 5, 0),
                'event_operator_name': self._extract_field_from_segment(evn_segment, 5, 1),
            })
        
        # Observation data (from OBX segments)
        if 'OBX' in segments:
            observations = []
            for obx_segment in segments['OBX']:
                observation = {
                    'set_id': self._extract_field_from_segment(obx_segment, 1, 0),
                    'value_type': self._extract_field_from_segment(obx_segment, 2, 0),
                    'observation_id': self._extract_field_from_segment(obx_segment, 3, 0),
                    'observation_text': self._extract_field_from_segment(obx_segment, 3, 1),
                    'observation_value': self._extract_field_from_segment(obx_segment, 5, 0),
                    'units': self._extract_field_from_segment(obx_segment, 6, 0),
                    'abnormal_flags': self._extract_field_from_segment(obx_segment, 8, 0),
                    'observation_datetime': self._extract_field_from_segment(obx_segment, 14, 0),
                }
                observations.append(observation)
            common_fields['observations'] = observations
        
        return common_fields
    
    def _extract_field_from_segment(self, segment: str, field_number: int, component: int = None) -> str:
        """
        Extract field from segment with improved component handling
        
        Args:
            segment: HL7 segment string
            field_number: Field number (1-based)
            component: Component number (0-based), None for entire field
            
        Returns:
            Field value or empty string
        """
        try:
            fields = segment.split('|')
            if field_number >= len(fields):
                return ''
            
            field_value = fields[field_number]
            
            # Return entire field if no component specified
            if component is None:
                return field_value
            
            # Handle component extraction
            if '^' in field_value:
                components = field_value.split('^')
                if component >= len(components):
                    return ''
                return components[component]
            else:
                # No components - return field if component 0 requested
                return field_value if component == 0 else ''
                
        except Exception:
            return ''
    
    def _extract_dot_notation_field(self, segments: Dict[str, List[str]], field_path: str, default: str) -> str:
        """Extract field using dot notation (e.g., PID.5.1.2)"""
        try:
            parts = field_path.split('.')
            segment_name = parts[0]
            field_number = int(parts[1]) if len(parts) > 1 else 1
            # Component is 1-based in HL7, but extract_segment_field expects 0-based, so subtract 1
            component = (int(parts[2]) - 1) if len(parts) > 2 else 0
            subcomponent = (int(parts[3]) - 1) if len(parts) > 3 else None

            if segment_name not in segments or not segments[segment_name]:
                return default

            # Use first occurrence of segment
            segment = segments[segment_name][0]
            field_value = hl7_mapper_service.extract_segment_field(segment, field_number, component)

            # Handle subcomponent if specified
            if subcomponent is not None and '&' in field_value:
                subcomponents = field_value.split('&')
                if subcomponent < len(subcomponents):
                    return subcomponents[subcomponent]
                return default

            return field_value if field_value else default

        except Exception:
            return default
    
    def _resolve_named_field_path(self, named_path: str, version: HL7Version) -> str:
        """Convert named field path to standard dot notation"""
        # Common named field mappings
        named_mappings = {
            'Patient.ID': 'PID.3.0',
            'Patient.Name.Last': 'PID.5.0', 
            'Patient.Name.First': 'PID.5.1',
            'Patient.Name.Middle': 'PID.5.2',
            'Patient.DOB': 'PID.7.0',
            'Patient.Gender': 'PID.8.0',
            'Patient.Address': 'PID.11.0',
            'Patient.Phone': 'PID.13.0',
            'Message.Type': 'MSH.9.0',
            'Message.Event': 'MSH.9.1',
            'Message.ControlID': 'MSH.10.0',
            'Message.DateTime': 'MSH.7.0',
            'Sending.Application': 'MSH.3.0',
            'Sending.Facility': 'MSH.4.0',
            'Event.Type': 'EVN.1.0',
            'Event.DateTime': 'EVN.2.0',
        }
        
        return named_mappings.get(named_path, named_path)
    
    def _load_field_definitions(self) -> Dict[str, List[HL7FieldDefinition]]:
        """Load comprehensive field definitions for all HL7 versions"""
        # This would typically load from a configuration file or database
        # For now, returning basic structure
        return {
            'MSH': [
                HL7FieldDefinition('MSH', 3, 0, 0, 'Sending Application', 'Sending Application Name'),
                HL7FieldDefinition('MSH', 9, 0, 0, 'Message Type', 'Message Type Code'),
                HL7FieldDefinition('MSH', 9, 1, 0, 'Trigger Event', 'Trigger Event Code'),
            ],
            'PID': [
                HL7FieldDefinition('PID', 3, 0, 0, 'Patient ID', 'Patient Identifier'),
                HL7FieldDefinition('PID', 5, 0, 0, 'Last Name', 'Patient Last Name'),
                HL7FieldDefinition('PID', 5, 1, 0, 'First Name', 'Patient First Name'),
                HL7FieldDefinition('PID', 7, 0, 0, 'Date of Birth', 'Patient Date of Birth'),
                HL7FieldDefinition('PID', 8, 0, 0, 'Gender', 'Patient Gender'),
            ]
        }
    
    def _load_common_field_mappings(self) -> Dict[str, str]:
        """Load mappings for commonly requested fields across versions"""
        return {
            'patient_id': 'PID.3.0',
            'patient_name': 'PID.5.0', 
            'patient_first': 'PID.5.1',
            'patient_dob': 'PID.7.0',
            'patient_gender': 'PID.8.0',
            'message_type': 'MSH.9.0',
            'control_id': 'MSH.10.0',
            'event_type': 'EVN.1.0',
        }
    
    def _parse_version_specific_fields(self, segments: Dict[str, List[str]], version: HL7Version) -> Dict[str, Any]:
        """Parse fields specific to detected HL7 version"""
        parsed_fields = {}
        
        # Version-specific parsing logic would go here
        # For now, return basic structure
        parsed_fields['version_detected'] = version.value
        parsed_fields['segment_count'] = sum(len(seg_list) for seg_list in segments.values())
        parsed_fields['unique_segments'] = list(segments.keys())
        
        return parsed_fields
    
    def _extract_xpath_field(self, segments: Dict[str, List[str]], xpath: str, default: str) -> str:
        """Extract field using XPath-like syntax"""
        # Basic XPath-like support for future enhancement
        # e.g., //PID[1]/field[5]/component[1]
        return default

# Global service instance
generic_hl7_mapper = GenericHL7Mapper()