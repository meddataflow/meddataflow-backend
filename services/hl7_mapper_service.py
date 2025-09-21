"""
HL7 Mapper Service - Integration with the existing hl7_mapper project
Provides functionality to leverage the hl7_mapper for transformations
"""
import sys
import os
from typing import Dict, List, Any, Optional
import json

# Conditional pandas import
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    pd = None

# Try importing from new in-repo location (backend.hl7_mapper)
try:
    from backend.hl7_mapper.mapper import Mapper
    from backend.hl7_mapper.hl7_structure import HL7Structure, CSVStructure
    from backend.hl7_mapper.base_mappers import BaseMapper
    from backend.hl7_mapper.transformers import transformations
    HL7_MAPPER_AVAILABLE = True
except ImportError as e:
    # Fallback: try legacy top-level location if running without package path
    try:
        from hl7_mapper.mapper import Mapper
        from hl7_mapper.hl7_structure import HL7Structure, CSVStructure
        from hl7_mapper.base_mappers import BaseMapper
        from hl7_mapper.transformers import transformations
        HL7_MAPPER_AVAILABLE = True
    except Exception as e2:
        HL7_MAPPER_AVAILABLE = False
        print(f"Warning: hl7_mapper not available ({e2}) - HL7 transformation activities will use fallback implementation")

class HL7MapperService:
    """Service for HL7 mapping and transformation using the hl7_mapper project"""
    
    def __init__(self):
        self.mapper_available = HL7_MAPPER_AVAILABLE
    
    def create_hl7_to_csv_mapping(self, hl7_message: str, csv_config: Dict[str, Any]) -> Optional[Any]:
        """
        Create CSV mapping from HL7 message using hl7_mapper
        
        Args:
            hl7_message: Raw HL7 message string
            csv_config: Configuration for CSV conversion including headers and mappings
            
        Returns:
            DataFrame with mapped data or None if mapping fails
        """
        if not self.mapper_available or not PANDAS_AVAILABLE:
            return self._fallback_hl7_to_csv(hl7_message, csv_config)
        
        try:
            # Create mapping DataFrame from config
            mapping_data = []
            headers = csv_config.get('headers', [])
            mappings = csv_config.get('mappings', {})
            
            for header in headers:
                mapping_info = mappings.get(header, {})
                source_location = mapping_info.get('source_location', '')
                transform = mapping_info.get('transform', '')
                
                mapping_data.append({
                    'Source_Location': source_location,
                    'Source_Type': 'HL7',
                    'Destination_Location': header,
                    'Destination_Type': 'CSV',
                    'Transform': transform
                })
            
            if not mapping_data:
                return None
                
            mapping_df = pd.DataFrame(mapping_data)
            
            # Create mapper instance
            mapper = Mapper(mapping_df, source_type='HL7', destination_type='CSV')
            
            # Prepare HL7 input data
            hl7_lines = hl7_message.split('\n')
            input_data = pd.DataFrame({'message': [hl7_message], 'segments': [hl7_lines]})
            
            # Execute mapping
            result = mapper.map(input_data)
            return result
            
        except Exception as e:
            print(f"Error in HL7 to CSV mapping: {e}")
            return self._fallback_hl7_to_csv(hl7_message, csv_config)
    
    def create_hl7_to_hl7_mapping(self, hl7_message: str, transform_config: Dict[str, Any]) -> Optional[str]:
        """
        Transform HL7 message to another HL7 format using hl7_mapper
        
        Args:
            hl7_message: Source HL7 message
            transform_config: Configuration for HL7 transformation mappings
            
        Returns:
            Transformed HL7 message string or None if transformation fails
        """
        if not self.mapper_available:
            return self._fallback_hl7_to_hl7(hl7_message, transform_config)
        
        try:
            # Create mapping DataFrame from config
            mapping_data = []
            mappings = transform_config.get('mappings', [])
            
            for mapping in mappings:
                mapping_data.append({
                    'Source_Location': mapping.get('source'),
                    'Source_Type': 'HL7',
                    'Destination_Location': mapping.get('target'),
                    'Destination_Type': 'HL7',
                    'Transform': mapping.get('transform', ''),
                    'Value': mapping.get('value', '')
                })
            
            if not mapping_data:
                return hl7_message  # Return original if no mappings
                
            mapping_df = pd.DataFrame(mapping_data)
            
            # Create mapper instance
            mapper = Mapper(mapping_df, source_type='HL7', destination_type='HL7')
            
            # Prepare input data
            input_data = pd.DataFrame({'message': [hl7_message]})
            
            # Execute mapping
            result = mapper.map(input_data)
            
            if not result.empty and 'transformed_message' in result.columns:
                return result['transformed_message'].iloc[0]
            
            return hl7_message
            
        except Exception as e:
            print(f"Error in HL7 to HL7 transformation: {e}")
            return self._fallback_hl7_to_hl7(hl7_message, transform_config)
    
    def _fallback_hl7_to_csv(self, hl7_message: str, csv_config: Dict[str, Any]) -> Any:
        """Fallback CSV conversion when hl7_mapper is not available"""
        headers = csv_config.get('headers', [])
        mappings = csv_config.get('mappings', {})
        
        # Simple fallback - create empty row with headers
        row_data = {}
        for header in headers:
            mapping_info = mappings.get(header, {})
            default_value = mapping_info.get('default_value', '')
            row_data[header] = default_value
        
        if PANDAS_AVAILABLE:
            return pd.DataFrame([row_data])
        else:
            # Return a simple dict representation when pandas is not available
            return {'data': [row_data], 'columns': headers}
    
    def _fallback_hl7_to_hl7(self, hl7_message: str, transform_config: Dict[str, Any]) -> str:
        """Fallback HL7 transformation when hl7_mapper is not available"""
        print("Using fallback HL7 transformation implementation")
        
        try:
            mappings = transform_config.get('mappings', [])
            if not mappings:
                return hl7_message
            
            # Parse the message into segments
            segments = self.parse_hl7_segments(hl7_message)
            transformed_segments = {}
            new_segments = {}  # For segments that don't exist yet (like ZPF)
            
            # Process each mapping
            for mapping in mappings:
                source = mapping.get('source', '')  # e.g., 'MSH.3'
                target = mapping.get('target', '')  # e.g., 'ZPF.1'  
                value = mapping.get('value', '')    # hardcoded value
                transform_type = mapping.get('transform', '')  # e.g., 'uppercase'
                
                if not target:
                    continue
                
                # Parse target to get segment, field, component
                target_parts = target.split('.')
                target_segment = target_parts[0]
                target_field = int(target_parts[1]) if len(target_parts) > 1 else 1
                target_component = int(target_parts[2]) if len(target_parts) > 2 else 0
                
                # Get the value to map
                if value:
                    # Use hardcoded value
                    map_value = value
                elif source:
                    # Extract from source field
                    map_value = self._extract_field_from_segment_by_path(segments, source)
                else:
                    continue
                
                # Apply transformation if specified
                if transform_type == 'uppercase':
                    map_value = map_value.upper()
                elif transform_type == 'lowercase':
                    map_value = map_value.lower()
                
                # Apply the mapping to target
                if target_segment in segments:
                    # Update existing segment
                    if target_segment not in transformed_segments:
                        transformed_segments[target_segment] = segments[target_segment][:]
                    
                    for i, segment in enumerate(transformed_segments[target_segment]):
                        updated_segment = self._set_field_in_segment(segment, target_field, target_component, map_value)
                        transformed_segments[target_segment][i] = updated_segment
                else:
                    # Create new segment (like ZPF)
                    if target_segment not in new_segments:
                        new_segments[target_segment] = [self._create_new_segment(target_segment)]
                    
                    # Set the field in the new segment
                    for i, segment in enumerate(new_segments[target_segment]):
                        updated_segment = self._set_field_in_segment(segment, target_field, target_component, map_value)
                        new_segments[target_segment][i] = updated_segment
            
            # Rebuild the HL7 message
            return self._rebuild_hl7_message(segments, transformed_segments, new_segments)
            
        except Exception as e:
            print(f"Error in fallback HL7 transformation: {e}")
            return hl7_message
    
    def parse_hl7_segments(self, hl7_message: str) -> Dict[str, List[str]]:
        """
        Parse HL7 message into segments
        
        Args:
            hl7_message: Raw HL7 message
            
        Returns:
            Dictionary with segment names as keys and list of segment instances as values
        """
        segments = {}
        lines = hl7_message.strip().split('\n')
        
        for line in lines:
            if not line.strip():
                continue
                
            segment_name = line[:3]  # First 3 characters are segment name
            
            if segment_name not in segments:
                segments[segment_name] = []
            
            segments[segment_name].append(line)
        
        return segments
    
    def extract_segment_field(self, segment: str, field_number: int, component: int = 0) -> str:
        """
        Extract field from HL7 segment
        
        Args:
            segment: HL7 segment string
            field_number: Field number (1-based HL7 standard)
            component: Component number within field (0-based)
            
        Returns:
            Field value or empty string if not found
        """
        try:
            fields = segment.split('|')
            
            # Use the same logic as the reference implementation in hl7_mapper/hl7_structure.py
            # For MSH: field_index = field - 1, for others: field_index = field
            # But we still need to handle the array indexing correctly
            if segment.startswith('MSH'):
                # Special case for MSH.1 which is always the field separator
                if field_number == 1:
                    return '|'
                # For MSH segments, field_number maps directly to array index
                # MSH.2 = fields[1] (encoding chars), MSH.3 = fields[2], etc.
                field_index = field_number - 1
                if field_index >= len(fields) or field_index < 0:
                    return ''
                field_value = fields[field_index]
            else:
                # For all other segments, field_number maps to array index field_number
                # PID.1 = fields[1], PID.2 = fields[2], etc. (fields[0] is segment name "PID")
                if field_number >= len(fields) or field_number <= 0:
                    return ''
                field_value = fields[field_number]
            
            # Handle component extraction
            if '^' in field_value:
                components = field_value.split('^')
                if component >= len(components) or component < 0:
                    return ''
                return components[component]
            else:
                # No components - return field if component 0 requested, empty otherwise
                return field_value if component == 0 else ''
            
        except Exception:
            return ''
    
    def _extract_field_from_segment_by_path(self, segments: Dict[str, List[str]], field_path: str) -> str:
        """Extract field value using path like 'MSH.3' from segments dict"""
        try:
            parts = field_path.split('.')
            segment_name = parts[0]
            field_number = int(parts[1]) if len(parts) > 1 else 1
            component = int(parts[2]) if len(parts) > 2 else 0
            
            if segment_name in segments and segments[segment_name]:
                segment = segments[segment_name][0]  # Use first occurrence
                return self.extract_segment_field(segment, field_number, component)
            
            return ''
        except Exception:
            return ''
    
    def _set_field_in_segment(self, segment: str, field_number: int, component: int, value: str) -> str:
        """Set field value in HL7 segment, creating fields as needed"""
        try:
            fields = segment.split('|')
            
            # Use the same field indexing logic as extract_segment_field
            if segment.startswith('MSH'):
                # For MSH segments, adjust field index by -1
                target_index = field_number - 1
            else:
                # For all other segments, use field_number directly as array index
                target_index = field_number
            
            # Ensure we have enough fields
            while len(fields) <= target_index:
                fields.append('')
            
            # Handle component setting
            if component > 0:
                # Split components and ensure we have enough
                components = fields[target_index].split('^') if fields[target_index] else ['']
                while len(components) <= component:
                    components.append('')
                
                components[component] = value
                fields[target_index] = '^'.join(components)
            else:
                # Set entire field
                fields[target_index] = value
            
            return '|'.join(fields)
            
        except Exception as e:
            print(f"Error setting field in segment: {e}")
            return segment
    
    def _create_new_segment(self, segment_name: str) -> str:
        """Create a new HL7 segment with given name"""
        return f"{segment_name}|"
    
    def _rebuild_hl7_message(self, original_segments: Dict[str, List[str]], 
                           transformed_segments: Dict[str, List[str]], 
                           new_segments: Dict[str, List[str]]) -> str:
        """Rebuild HL7 message from segments"""
        try:
            message_lines = []
            
            # Add segments in typical HL7 order
            segment_order = ['MSH', 'EVN', 'PID', 'PV1', 'ORC', 'OBR', 'OBX', 'NTE', 'ZPF', 'ZCF']
            
            processed_segments = set()
            
            # Add segments in order
            for segment_name in segment_order:
                # Check transformed segments first
                if segment_name in transformed_segments:
                    message_lines.extend(transformed_segments[segment_name])
                    processed_segments.add(segment_name)
                # Then check original segments
                elif segment_name in original_segments:
                    message_lines.extend(original_segments[segment_name])
                    processed_segments.add(segment_name)
                # Finally check new segments
                elif segment_name in new_segments:
                    message_lines.extend(new_segments[segment_name])
                    processed_segments.add(segment_name)
            
            # Add any remaining original segments not in standard order
            for segment_name, segment_list in original_segments.items():
                if segment_name not in processed_segments:
                    if segment_name in transformed_segments:
                        message_lines.extend(transformed_segments[segment_name])
                    else:
                        message_lines.extend(segment_list)
                    processed_segments.add(segment_name)
            
            # Add any remaining new segments not in standard order
            for segment_name, segment_list in new_segments.items():
                if segment_name not in processed_segments:
                    message_lines.extend(segment_list)
            
            return '\n'.join(message_lines)
            
        except Exception as e:
            print(f"Error rebuilding HL7 message: {e}")
            return '\n'.join([seg for seg_list in original_segments.values() for seg in seg_list])

# Global service instance
hl7_mapper_service = HL7MapperService()
