"""
Multi-format healthcare message model and repository.
Supports HL7, FHIR, DICOM, X12, NCPDP, CDA, CCD, CCR and other formats.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid
import base64
import json
import xml.etree.ElementTree as ET
from pathlib import Path
import re
from database.connection import fetch_one, fetch_all, execute_returning, execute

class MessageFormat(str, Enum):
    HL7 = "hl7"
    FHIR = "fhir"
    DICOM = "dicom"
    X12 = "x12"
    NCPDP = "ncpdp"
    CDA = "cda"
    CCD = "ccd"
    CCR = "ccr"
    CUSTOM = "custom"

class MessageStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"
    IGNORED = "IGNORED"

class MessageDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    INTERNAL = "INTERNAL"

class InteropMessageRepository:
    """Repository for managing multi-format healthcare messages"""

    @staticmethod
    async def create_message(
        tenant_id: uuid.UUID,
        message_format: str,
        raw_message: Optional[str] = None,
        binary_payload: Optional[bytes] = None,
        message_type: Optional[str] = None,
        created_by_id: Optional[uuid.UUID] = None,
        workflow_id: Optional[uuid.UUID] = None,
        vendor_endpoint_id: Optional[uuid.UUID] = None,
        source_endpoint: Optional[str] = None,
        message_direction: str = "INBOUND",
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        **additional_fields
    ) -> Dict[str, Any]:
        """Create a new multi-format message"""
        message_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # Handle binary payloads (e.g., DICOM)
        file_size = len(binary_payload) if binary_payload else (len(raw_message.encode('utf-8')) if raw_message else 0)

        inferred_type = InteropMessageRepository._infer_message_type(
            message_format=message_format,
            raw_message=raw_message,
            binary_payload=binary_payload,
            file_name=file_name or additional_fields.get('file_name'),
            supplied_type=message_type
        )
        resolved_message_type = inferred_type or message_type or 'UNKNOWN'

        query = """
        INSERT INTO hl7_messages (
            id, tenant_id, created_by_id, workflow_id, vendor_endpoint_id,
            message_format, message_control_id, message_type, event_type, hl7_version,
            raw_message, binary_payload, parsed_message, english_translation,
            file_name, file_size, mime_type,
            encoding_characters, field_separator,
            sending_application, sending_facility, receiving_application, receiving_facility,
            status, direction, source_endpoint, destination_endpoint,
            created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17,
            $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $28
        )
        RETURNING *
        """

        return await execute_returning(
            query,
            message_id, tenant_id, created_by_id, workflow_id, vendor_endpoint_id,
            message_format,
            additional_fields.get('message_control_id'),
            resolved_message_type,
            additional_fields.get('event_type'),
            additional_fields.get('hl7_version'),
            raw_message,
            binary_payload,
            additional_fields.get('parsed_message'),
            additional_fields.get('english_translation'),
            file_name,
            file_size,
            mime_type,
            additional_fields.get('encoding_characters'),
            additional_fields.get('field_separator'),
            additional_fields.get('sending_application'),
            additional_fields.get('sending_facility'),
            additional_fields.get('receiving_application'),
            additional_fields.get('receiving_facility'),
            additional_fields.get('status', MessageStatus.RECEIVED.value),
            message_direction,
            source_endpoint or additional_fields.get('source_endpoint'),
            additional_fields.get('destination_endpoint'),
            now
        )

    @staticmethod
    def _infer_message_type(
        message_format: Optional[str],
        raw_message: Optional[str] = None,
        binary_payload: Optional[bytes] = None,
        file_name: Optional[str] = None,
        supplied_type: Optional[str] = None
    ) -> Optional[str]:
        """
        Infer a human-readable message type when the caller did not supply one.
        Attempts lightweight parsing per format to populate dashboards with richer context.
        """
        fmt = (message_format or "").strip().lower()
        supplied_upper = (supplied_type or "").strip().upper()

        # Respect explicit classifications that aren't generic placeholders
        if supplied_upper and supplied_upper not in {"UNKNOWN", "FILE_UPLOAD"}:
            return supplied_type

        # Binary payloads (e.g., DICOM) should stay marked as file uploads
        if binary_payload and fmt == "dicom":
            return "FILE_UPLOAD"

        text = (raw_message or "").strip()
        if not text:
            # Fall back to file name stem if we have nothing else
            if file_name:
                stem = Path(file_name).stem
                return stem.upper() if stem else None
            return None

        try:
            if fmt == "fhir":
                parsed = json.loads(text)
                # Direct resource
                if isinstance(parsed, dict):
                    resource_type = parsed.get("resourceType")
                    if resource_type:
                        return str(resource_type).upper()
                    # Bundle entry
                    entries = parsed.get("entry")
                    if isinstance(entries, list):
                        for entry in entries:
                            if isinstance(entry, dict):
                                resource = entry.get("resource")
                                if isinstance(resource, dict) and resource.get("resourceType"):
                                    return str(resource["resourceType"]).upper()
                    # Composition document type
                    doc_type = parsed.get("type")
                    if isinstance(doc_type, str):
                        return doc_type.upper()
                elif isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and item.get("resourceType"):
                            return str(item["resourceType"]).upper()
                return "FHIR"

            if fmt in {"cda", "ccd", "ccr"} or text.startswith("<"):
                root = ET.fromstring(text)
                # Strip namespace
                tag = root.tag.split("}")[-1].lower()
                if fmt == "ccr" or tag == "continuityofcarerecord":
                    return "CCR"
                if fmt == "ccd":
                    return "CCD"
                if fmt == "cda" or tag == "clinicaldocument":
                    code_elem = root.find("{urn:hl7-org:v3}code")
                    if code_elem is not None:
                        display_name = code_elem.get("displayName")
                        if display_name:
                            return display_name
                        code_val = code_elem.get("code")
                        if code_val:
                            return f"CDA-{code_val}"
                    return "CDA"
                # Generic XML such as NCPDP SCRIPT documents
                if fmt == "ncpdp":
                    tag_name = tag.upper()
                    if tag_name:
                        return tag_name
                return tag.upper() if tag else None

            if fmt == "ncpdp":
                if text.startswith("<"):
                    root = ET.fromstring(text)
                    return root.tag.split("}")[-1].upper()
                first_line = text.splitlines()[0].strip()
                if first_line:
                    return first_line.split("|")[0].strip().upper()
                return "NCPDP"

            if fmt == "x12":
                # Segments typically separated by ~ or newline
                segments = re.split(r"[~\n\r]+", text)
                for seg in segments:
                    seg = seg.strip()
                    if not seg:
                        continue
                    if seg.startswith("ST*"):
                        parts = seg.split("*")
                        if len(parts) > 1 and parts[1]:
                            return f"X12-{parts[1]}"
                    if seg.startswith("GS*"):
                        parts = seg.split("*")
                        if len(parts) > 1 and parts[1]:
                            return f"X12-{parts[1]}"
                return "X12"

            if fmt == "dicom":
                return "FILE_UPLOAD"

        except Exception:
            # Ignore parsing issues and fall through to filename fallback
            pass

        if file_name:
            stem = Path(file_name).stem
            if stem:
                return stem.upper()

        return None

    @staticmethod
    async def get_message_by_id(message_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Get message by ID with binary payload encoded as base64"""
        query = """
        SELECT m.id, m.tenant_id, m.created_by_id, m.workflow_id, m.vendor_endpoint_id,
               m.message_format, m.message_control_id, m.message_type, m.event_type,
               m.hl7_version, m.raw_message, m.parsed_message, m.english_translation,
               m.file_name, m.file_size, m.mime_type,
               m.encoding_characters, m.field_separator,
               m.sending_application, m.sending_facility,
               m.receiving_application, m.receiving_facility,
               m.status, m.direction, m.source_endpoint, m.destination_endpoint,
               m.processed_at, m.retry_count, m.created_at, m.updated_at,
               m.processing_errors, m.validation_errors,
               u.first_name || ' ' || u.last_name as created_by_name,
               w.name as workflow_name,
               ve.vendor_name
        FROM hl7_messages m
        LEFT JOIN users u ON m.created_by_id = u.id
        LEFT JOIN workflows w ON m.workflow_id = w.id
        LEFT JOIN vendor_endpoints ve ON m.vendor_endpoint_id = ve.id
        WHERE m.id = $1
        """
        result = await fetch_one(query, message_id)
        if result:
            result_dict = dict(result)
            # If there's a binary payload, encode it separately in Python
            if result_dict.get('id'):
                binary_query = "SELECT binary_payload FROM hl7_messages WHERE id = $1"
                binary_result = await fetch_one(binary_query, message_id)
                if binary_result and binary_result.get('binary_payload'):
                    import base64
                    result_dict['binary_payload'] = base64.b64encode(binary_result['binary_payload']).decode('utf-8')
                else:
                    result_dict['binary_payload'] = None
            return result_dict
        return None

    @staticmethod
    async def get_messages_by_tenant(
        tenant_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        status: Optional[MessageStatus] = None,
        message_format: Optional[str] = None,
        message_type: Optional[str] = None,
        exclude_format: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get messages for a tenant with filters"""
        conditions = ["m.tenant_id = $1"]
        params = [tenant_id]
        param_count = 2

        if status:
            conditions.append(f"m.status = ${param_count}")
            params.append(status.value)
            param_count += 1

        if message_format:
            conditions.append(f"m.message_format = ${param_count}")
            params.append(message_format)
            param_count += 1

        if exclude_format:
            conditions.append(f"m.message_format != ${param_count}")
            params.append(exclude_format)
            param_count += 1

        if message_type:
            conditions.append(f"m.message_type = ${param_count}")
            params.append(message_type)
            param_count += 1

        if start_date:
            conditions.append(f"m.created_at >= ${param_count}")
            params.append(start_date)
            param_count += 1

        if end_date:
            conditions.append(f"m.created_at <= ${param_count}")
            params.append(end_date)
            param_count += 1

        where_clause = " AND ".join(conditions)

        query = f"""
        SELECT m.id, m.tenant_id, m.created_by_id, m.workflow_id, m.vendor_endpoint_id,
               m.message_format, m.message_control_id, m.message_type, m.event_type,
               m.hl7_version, m.status, m.direction, m.file_name, m.file_size, m.mime_type,
               m.source_endpoint, m.destination_endpoint, m.created_at, m.updated_at, m.processed_at,
               CASE
                   WHEN m.binary_payload IS NOT NULL THEN '[BINARY]'
                   WHEN LENGTH(m.raw_message) > 500 THEN SUBSTRING(m.raw_message, 1, 500) || '...'
                   ELSE m.raw_message
               END as raw_message_preview,
               u.first_name || ' ' || u.last_name as created_by_name,
               w.name as workflow_name,
               ve.vendor_name
        FROM hl7_messages m
        LEFT JOIN users u ON m.created_by_id = u.id
        LEFT JOIN workflows w ON m.workflow_id = w.id
        LEFT JOIN vendor_endpoints ve ON m.vendor_endpoint_id = ve.id
        WHERE {where_clause}
        ORDER BY m.created_at DESC
        LIMIT ${param_count} OFFSET ${param_count + 1}
        """

        params.extend([limit, offset])
        return await fetch_all(query, *params)

    @staticmethod
    async def update_message_status(
        message_id: uuid.UUID,
        status: MessageStatus,
        processing_errors: Optional[Dict] = None,
        validation_errors: Optional[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """Update message status and errors"""
        now = datetime.now(timezone.utc)

        query = """
        UPDATE hl7_messages
        SET status = $1, processing_errors = $2, validation_errors = $3,
            processed_at = $4, updated_at = $4
        WHERE id = $5
        RETURNING *
        """

        processed_at = now if status == MessageStatus.PROCESSED else None

        return await execute_returning(
            query, status.value, processing_errors, validation_errors,
            processed_at, message_id
        )

    @staticmethod
    async def update_message_parsed_data(
        message_id: uuid.UUID,
        parsed_message: Dict[str, Any],
        english_translation: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Update message parsed data and English translation"""
        query = """
        UPDATE hl7_messages
        SET parsed_message = $1, english_translation = $2, updated_at = $3
        WHERE id = $4
        RETURNING *
        """

        return await execute_returning(
            query, parsed_message, english_translation,
            datetime.now(timezone.utc), message_id
        )

    @staticmethod
    async def delete_message(message_id: uuid.UUID) -> bool:
        """Delete a specific message"""
        query = "DELETE FROM hl7_messages WHERE id = $1"
        result = await execute(query, message_id)
        return bool(result)

    @staticmethod
    async def get_message_stats(tenant_id: uuid.UUID) -> Dict[str, Any]:
        """Get message statistics for a tenant across all formats"""
        query = """
        SELECT
            COUNT(*) as total_messages,
            COUNT(CASE WHEN status = 'RECEIVED' THEN 1 END) as received,
            COUNT(CASE WHEN status = 'PROCESSING' THEN 1 END) as processing,
            COUNT(CASE WHEN status = 'PROCESSED' THEN 1 END) as processed,
            COUNT(CASE WHEN status = 'FAILED' THEN 1 END) as failed,
            COUNT(CASE WHEN created_at >= CURRENT_DATE THEN 1 END) as today,
            COUNT(DISTINCT message_format) as unique_formats,
            COUNT(DISTINCT message_type) as unique_message_types,
            COUNT(CASE WHEN message_format = 'hl7' THEN 1 END) as hl7_count,
            COUNT(CASE WHEN message_format = 'fhir' THEN 1 END) as fhir_count,
            COUNT(CASE WHEN message_format = 'dicom' THEN 1 END) as dicom_count,
            COUNT(CASE WHEN message_format = 'x12' THEN 1 END) as x12_count,
            COUNT(CASE WHEN message_format = 'ncpdp' THEN 1 END) as ncpdp_count,
            AVG(CASE WHEN processed_at IS NOT NULL AND created_at IS NOT NULL
                THEN EXTRACT(EPOCH FROM (processed_at - created_at)) END) as avg_processing_time_seconds
        FROM hl7_messages
        WHERE tenant_id = $1
        """
        return await fetch_one(query, tenant_id) or {}
