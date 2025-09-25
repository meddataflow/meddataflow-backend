"""
HL7 Mapper Service - Streamlined implementation
Provides core HL7 parsing and field extraction functionality
"""
import re
from typing import Dict, List, Any, Optional
from datetime import datetime


class HL7MapperService:
    """
    Streamlined HL7 mapping service focused on the most commonly used functionality.

    This service provides:
    - HL7 message parsing into segments
    - Field and component extraction from segments
    - Basic HL7-to-HL7 transformations
    """

    def __init__(self):
        """Initialize the HL7 mapper service."""
        pass

    # ==================== CORE HL7 PARSING ====================

    def parse_hl7_segments(self, hl7_message: str) -> Dict[str, List[str]]:
        """
        Parse HL7 message into segments.

        Args:
            hl7_message: Raw HL7 message string

        Returns:
            Dictionary with segment names as keys and list of segment instances as values
            Example: {'MSH': ['MSH|...'], 'PID': ['PID|...']}
        """
        if not hl7_message or not isinstance(hl7_message, str):
            return {}

        segments = {}
        lines = hl7_message.strip().split('\n')

        for line in lines:
            line = line.strip()
            if not line or len(line) < 3:
                continue

            # First 3 characters are the segment name
            segment_name = line[:3]

            if not segment_name.isalnum():
                continue

            if segment_name not in segments:
                segments[segment_name] = []

            segments[segment_name].append(line)

        return segments

    def extract_segment_field(self, segment: str, field_number: int, component: Optional[int] = 0) -> str:
        """
        Extract field from HL7 segment with component support.

        Args:
            segment: HL7 segment string (e.g., "MSH|^~\\&|APP|FACILITY|...")
            field_number: Field number (1-based HL7 standard)
            component: Component number within field (0-based)

        Returns:
            Field value or empty string if not found

        Examples:
            extract_segment_field("MSH|^~\\&|SYSTEM|HOSPITAL", 3, 0) -> "SYSTEM"
            extract_segment_field("PID|1||123^456^MR", 3, 1) -> "456"
        """
        if not segment or not isinstance(segment, str):
            return ''

        try:
            fields = segment.split('|')

            # Handle MSH segment special case
            if segment.startswith('MSH'):
                # MSH.1 is always the field separator
                if field_number == 1:
                    return '|'
                # For MSH segments, field numbering is offset by -1
                field_index = field_number - 1
                if field_index >= len(fields) or field_index < 0:
                    return ''
                field_value = fields[field_index]
            else:
                # For all other segments, field_number maps directly to array index
                if field_number >= len(fields) or field_number <= 0:
                    return ''
                field_value = fields[field_number]

            # Handle component extraction
            # component semantics:
            # - None => return entire field
            # - 0 => first component
            # - 1..n => subsequent components
            if component is None:
                return field_value
            if '^' in field_value:
                components = field_value.split('^')
                if 0 <= component < len(components):
                    return components[component]
                return ''
            # No component separator present
            return field_value if component == 0 else ''

        except (IndexError, ValueError, AttributeError):
            return ''

    # ==================== HL7 TRANSFORMATIONS ====================

    def create_hl7_to_hl7_mapping(self, hl7_message: str, transform_config: Dict[str, Any]) -> Optional[str]:
        """
        Transform HL7 message to another HL7 format using simple field mappings.

        Args:
            hl7_message: Source HL7 message
            transform_config: Configuration dict with 'mappings' list containing:
                - source: Source field path (e.g., 'MSH.3')
                - target: Target field path (e.g., 'ZPF.1')
                - value: Optional hardcoded value to use instead of source
                - transform: Optional transformation ('uppercase', 'lowercase')

        Returns:
            Transformed HL7 message string or original if transformation fails

        Example transform_config:
        {
            "mappings": [
                {"source": "MSH.3", "target": "ZPF.1"},
                {"value": "CUSTOM", "target": "ZPF.2", "transform": "uppercase"}
            ]
        }
        """
        if not hl7_message or not transform_config:
            return hl7_message

        try:
            mappings = transform_config.get('mappings', [])
            if not mappings:
                return hl7_message

            # Parse the message into segments
            segments = self.parse_hl7_segments(hl7_message)
            transformed_segments = {}
            new_segments = {}

            # Process each mapping
            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue

                source = mapping.get('source', '')
                target = mapping.get('target', '')
                value = mapping.get('value', '')
                transform_type = mapping.get('transform', '')

                if not target:
                    continue

                # Parse target to get segment, field, component
                target_parts = target.split('.')
                target_segment = target_parts[0]
                target_field = int(target_parts[1]) if len(target_parts) > 1 else 1
                # Components in paths are 1-based; convert to 0-based. If not provided, None => set whole field
                target_component = (int(target_parts[2]) - 1) if len(target_parts) > 2 else None
                if isinstance(target_component, int) and target_component < 0:
                    target_component = 0

                # Get the value to map
                if value:
                    map_value = value
                elif source:
                    map_value = self._extract_field_by_path(segments, source)
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
                    # Create new segment
                    if target_segment not in new_segments:
                        new_segments[target_segment] = [f"{target_segment}|"]

                    for i, segment in enumerate(new_segments[target_segment]):
                        updated_segment = self._set_field_in_segment(segment, target_field, target_component, map_value)
                        new_segments[target_segment][i] = updated_segment

            # Rebuild the HL7 message
            return self._rebuild_hl7_message(hl7_message, segments, transformed_segments, new_segments)

        except Exception as e:
            print(f"Error in HL7 transformation: {type(e).__name__}: {e}")
            return hl7_message

    # ==================== HELPER METHODS ====================

    def _extract_field_by_path(self, segments: Dict[str, List[str]], field_path: str) -> str:
        """
        Extract field value using path like 'MSH.3' from segments dict.

        Args:
            segments: Parsed segments dictionary
            field_path: Field path like 'MSH.3' or 'PID.5.1'

        Returns:
            Extracted field value or empty string
        """
        try:
            parts = field_path.split('.')
            segment_name = parts[0]
            field_number = int(parts[1]) if len(parts) > 1 else 1
            # Interpret component in path as 1-based (HL7 convention), convert to 0-based for extractor
            component = (int(parts[2]) - 1) if len(parts) > 2 else 0
            if component < 0:
                component = 0

            if segment_name in segments and segments[segment_name]:
                segment = segments[segment_name][0]  # Use first occurrence
                return self.extract_segment_field(segment, field_number, component)

            return ''
        except (ValueError, IndexError, KeyError):
            return ''

    def _set_field_in_segment(self, segment: str, field_number: int, component: Optional[int], value: str) -> str:
        """
        Set field value in HL7 segment, creating fields as needed.

        Args:
            segment: HL7 segment string
            field_number: Field number (1-based)
            component: Component number (0-based)
            value: Value to set

        Returns:
            Updated segment string
        """
        try:
            fields = segment.split('|')

            # Use the same field indexing logic as extract_segment_field
            if segment.startswith('MSH'):
                target_index = field_number - 1
            else:
                target_index = field_number

            # Ensure we have enough fields
            while len(fields) <= target_index:
                fields.append('')

            # Handle component setting
            if component is None:
                # Set entire field value
                fields[target_index] = value
            else:
                # Set specific component (0-based)
                components = fields[target_index].split('^') if fields[target_index] else ['']
                while len(components) <= component:
                    components.append('')
                components[component] = value
                fields[target_index] = '^'.join(components)

            return '|'.join(fields)

        except Exception:
            return segment

    def _rebuild_hl7_message(self, original_hl7: str, original_segments: Dict[str, List[str]],
                           transformed_segments: Dict[str, List[str]],
                           new_segments: Dict[str, List[str]]) -> str:
        """
        Rebuild HL7 message from segments in proper order.

        Args:
            original_hl7: Original HL7 message to preserve order
            original_segments: Original parsed segments
            transformed_segments: Modified segments
            new_segments: Newly created segments

        Returns:
            Rebuilt HL7 message string
        """
        try:
            message_lines = []

            # Preserve original message order instead of enforcing a fixed order
            # This ensures all HL7 versions and segment types are preserved
            original_lines = original_hl7.strip().split('\n')
            original_order = []

            # Extract segment order from original message
            for line in original_lines:
                line = line.strip()
                if line and len(line) >= 3:
                    segment_name = line[:3]
                    if segment_name.isalnum() and segment_name not in original_order:
                        original_order.append(segment_name)

            processed_segments = set()

            # Add segments in original message order to preserve structure
            for segment_name in original_order:
                # Check transformed segments first
                if segment_name in transformed_segments:
                    message_lines.extend(transformed_segments[segment_name])
                    processed_segments.add(segment_name)
                # Then original segments
                elif segment_name in original_segments:
                    message_lines.extend(original_segments[segment_name])
                    processed_segments.add(segment_name)

            # Add any new segments at the end
            all_segment_names = set(transformed_segments.keys()) | set(new_segments.keys())
            for segment_name in sorted(all_segment_names - processed_segments):
                if segment_name in new_segments:
                    message_lines.extend(new_segments[segment_name])

            return '\n'.join(message_lines)

        except Exception:
            # Fallback: just concatenate all original segments
            return '\n'.join([seg for seg_list in original_segments.values() for seg in seg_list])

    # ==================== FALLBACK METHODS ====================

    def create_hl7_to_csv_mapping(self, hl7_message: str, csv_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Create CSV mapping from HL7 message (fallback implementation).

        Args:
            hl7_message: Raw HL7 message string
            csv_config: Configuration for CSV conversion

        Returns:
            Simple dictionary representation of CSV data
        """
        headers = csv_config.get('headers', [])
        mappings = csv_config.get('mappings', {})

        # Parse segments for field extraction
        segments = self.parse_hl7_segments(hl7_message)
        row_data = {}

        for header in headers:
            mapping_info = mappings.get(header, {})
            source_location = mapping_info.get('source_location', '')
            default_value = mapping_info.get('default_value', '')

            if source_location:
                # Extract value from HL7 message
                value = self._extract_field_by_path(segments, source_location)
                row_data[header] = value if value else default_value
            else:
                row_data[header] = default_value

        return {'data': [row_data], 'columns': headers}


# ==================== GLOBAL SERVICE INSTANCE ====================

# Global service instance for backward compatibility
hl7_mapper_service = HL7MapperService()
