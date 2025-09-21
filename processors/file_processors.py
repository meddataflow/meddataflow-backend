"""
File and Data Processing Activity Processors

This module contains processors for file operations and data conversion activities
extracted from the workflow execution service.
"""

import asyncio
import json
import csv
import io
import re
import logging
from typing import Dict, Any

# Import dependencies for file operations
import aiofiles
import aioftp
import asyncssh

from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus
from services.hl7_parser import ParsedHL7Message
from services.s3_service import s3_service
from database.connection import fetch_one_dict, fetch_all_dict, execute_dict

logger = logging.getLogger(__name__)


def _extract_hl7_value(message: ParsedHL7Message, path: str) -> str:
    """
    Extract value from HL7 message using path notation
    Examples: MSH.3, PID.5.1, OBX[0].5
    """
    try:
        parts = path.split('.')
        segment_name = parts[0]

        # Find segment
        segment = None
        for seg in message.segments:
            if seg.type == segment_name:
                segment = seg
                break

        if not segment:
            return ""

        # Navigate to field
        if len(parts) > 1:
            field_index = int(parts[1]) - 1  # HL7 uses 1-based indexing
            if field_index < len(segment.fields):
                field = segment.fields[field_index]

                # Handle component if specified
                if len(parts) > 2 and '^' in field.value:
                    components = field.value.split('^')
                    component_index = int(parts[2]) - 1
                    if component_index < len(components):
                        return components[component_index]

                return field.value

        return ""

    except Exception as e:
        logger.warning(f"Failed to extract HL7 value from path {path}: {e}")
        return ""


async def process_message_transformer_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """Process Message Transformer activity"""
    config = activity.get("config", {})
    transformation_rules = config.get("transformation_rules", [])
    output_format = config.get("output_format", "json")

    transformed_data = {}

    for rule in transformation_rules:
        # Support both UI shapes: {source,target,operation} and backend shape {source_path,target_path,transformation}
        source_path = rule.get("source_path") or rule.get("source")
        target_path = rule.get("target_path") or rule.get("target")
        transformation = rule.get("transformation") or rule.get("operation", "direct")

        # Extract value from HL7 message
        if context.message and source_path:
            value = _extract_hl7_value(context.message, source_path)

            # Apply transformation
            if transformation == "uppercase":
                value = str(value).upper()
            elif transformation == "lowercase":
                value = str(value).lower()
            elif transformation == "clean":
                value = re.sub(r'[^a-zA-Z0-9\s]', '', str(value))

            transformed_data[target_path] = value

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={
            "transformed_data": transformed_data,
            "output_format": output_format
        },
        variables=transformed_data
    )


async def process_file_writer_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
    ) -> ActivityResult:
    """Process File Writer activity with FTP/SFTP/Local file system support"""
    config = activity.get("config", {})
    connection_config = config.get("connection", {})
    file_config = config.get("file", {})

    # Connection settings
    protocol = connection_config.get("protocol", "local")  # local, ftp, sftp
    host = connection_config.get("host", "")
    port = connection_config.get("port", 22 if protocol == "sftp" else 21)
    username = connection_config.get("username", "")
    password = connection_config.get("password", "")

    # File settings
    file_path = file_config.get("file_path", "/tmp/output.txt")
    content = file_config.get("content", "")
    file_format = file_config.get("format", "text")
    encoding = file_config.get("encoding", "utf-8")
    selected_variables = file_config.get("selected_variables", [])

    # Prepare content from selected variables or custom content
    if selected_variables:
        # Create content from selected variables
        if file_format == "json":
            content_data = {}
            for var_name in selected_variables:
                if var_name in context.variables:
                    content_data[var_name] = context.variables[var_name]
            content = json.dumps(content_data, indent=2)
        elif file_format == "csv":
            output = io.StringIO()
            if selected_variables:
                writer = csv.writer(output)
                writer.writerow(selected_variables)  # Header
                values = [str(context.variables.get(var, "")) for var in selected_variables]
                writer.writerow(values)
                content = output.getvalue()
        else:
            # Plain text format
            content_lines = []
            for var_name in selected_variables:
                if var_name in context.variables:
                    content_lines.append(f"{var_name}: {context.variables[var_name]}")
            content = "\n".join(content_lines)

    # Variable substitution in content and file path
    import datetime

    # Add default timestamp if not present
    if "timestamp" not in context.variables:
        context.variables["timestamp"] = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    for var_name, var_value in context.variables.items():
        # Handle both single {var} and double {{var}} curly braces
        single_placeholder = f"{{{var_name}}}"
        double_placeholder = f"{{{{{var_name}}}}}"

        # Replace in content
        if single_placeholder in content:
            content = content.replace(single_placeholder, str(var_value))
        if double_placeholder in content:
            content = content.replace(double_placeholder, str(var_value))

        # Replace in file path
        if single_placeholder in file_path:
            file_path = file_path.replace(single_placeholder, str(var_value))
        if double_placeholder in file_path:
            file_path = file_path.replace(double_placeholder, str(var_value))

    try:
        bytes_written = 0
        final_path = file_path

        if protocol == "local":
            # Local file system
            # Ensure directory exists
            import os
            directory = os.path.dirname(file_path)
            if directory:
                os.makedirs(directory, exist_ok=True)

            async with aiofiles.open(file_path, 'w', encoding=encoding) as f:
                await f.write(content)
                bytes_written = len(content.encode(encoding))

        elif protocol == "ftp":
            # FTP upload
            async with aioftp.Client(host=host, port=port) as client:
                await client.login(username, password)

                # Ensure directory exists
                directory = "/".join(file_path.split("/")[:-1])
                if directory:
                    try:
                        await client.make_directory(directory)
                    except:
                        pass  # Directory might already exist

                # Upload file
                content_bytes = content.encode(encoding)
                await client.upload_bytes(content_bytes, file_path)
                bytes_written = len(content_bytes)

        elif protocol == "sftp":
            # SFTP upload
            async with asyncssh.connect(host, port=port, username=username, password=password) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Ensure directory exists
                    directory = "/".join(file_path.split("/")[:-1])
                    if directory:
                        try:
                            await sftp.makedirs(directory)
                        except:
                            pass  # Directory might already exist

                    # Upload file
                    await sftp.put(io.BytesIO(content.encode(encoding)), file_path)
                    bytes_written = len(content.encode(encoding))

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": f"File written successfully via {protocol.upper()}",
                "protocol": protocol,
                "file_path": final_path,
                "bytes_written": bytes_written,
                "format": file_format,
                "variables_used": list(selected_variables) if selected_variables else "all"
            },
            variables={
                "file_written": True,
                "file_path": final_path,
                "file_bytes": bytes_written
            }
        )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"File writing failed ({protocol}): {str(e)}"
        )


async def process_csv_batcher_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Buffer CSV rows and flush to S3 when thresholds are met.

    Expects variables set by previous CSV activity:
      - context.variables.csv_row: Dict[str, Any]
      - context.variables.csv_headers: List[str]

    Config shape:
      {
        "group_key_template": "hl7/{tenant_id}/{date}/workflow-{workflow_id}.csv",
        "thresholds": { "max_rows": 1000, "max_age_seconds": 300, "max_bytes": 5000000 },
        "s3": { "bucket": "hl7-processed-files", "key_template": null },
        "dedupe_on_message_id": true
      }
    """
    import json as _json
    from datetime import datetime, timedelta

    cfg = activity.get("config", {})
    thresholds = cfg.get("thresholds", {}) or {}
    max_rows = int(thresholds.get("max_rows", 1000))
    max_age_seconds = int(thresholds.get("max_age_seconds", 300))
    max_bytes = int(thresholds.get("max_bytes", 5_000_000))
    dedupe = bool(cfg.get("dedupe_on_message_id", True))

    # Ensure buffer table exists (idempotent)
    try:
        await execute_dict(
            """
            CREATE TABLE IF NOT EXISTS csv_batch_rows (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
                workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
                group_key TEXT NOT NULL,
                headers JSONB NOT NULL,
                row JSONB NOT NULL,
                message_id VARCHAR(255),
                execution_id VARCHAR(255),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                flushed_at TIMESTAMP WITH TIME ZONE,
                flush_key TEXT
            )
            """,
            {}
        )
        await execute_dict(
            "CREATE INDEX IF NOT EXISTS idx_csv_batch_rows_group ON csv_batch_rows(tenant_id, workflow_id, group_key, flushed_at)",
            {}
        )
    except Exception:
        # Best effort; if it already exists or permissions limited, continue
        pass

    # Determine group key via template
    def substitute(template: str) -> str:
        if not template:
            return template
        date_str = datetime.utcnow().strftime('%Y-%m-%d')
        ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        pairs = {
            'tenant_id': str(context.tenant_id),
            'workflow_id': str(context.workflow_id),
            'execution_id': str(context.execution_id),
            'date': date_str,
            'timestamp': ts,
            **{k: str(v) for k, v in (context.variables or {}).items()}
        }
        out = template
        for k, v in pairs.items():
            out = out.replace(f'{{{k}}}', v).replace(f'{{{{{k}}}}}', v)
        return out

    group_tpl = cfg.get("group_key_template", "hl7/{tenant_id}/{date}/workflow-{workflow_id}.csv")
    group_key = substitute(group_tpl)

    # Extract row and headers
    row = context.variables.get("csv_row")
    headers = context.variables.get("csv_headers")

    if not row or not headers:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="csv_batcher requires csv_row and csv_headers variables from a prior CSV activity"
        )

    message_id = context.variables.get("MESSAGE_CONTROL_ID")

    # Optionally dedupe on message_id for idempotency
    if dedupe and message_id:
        exists = await fetch_one_dict(
            "SELECT id FROM csv_batch_rows WHERE tenant_id = :tenant_id AND workflow_id = :workflow_id AND group_key = :group_key AND message_id = :mid AND flushed_at IS NULL",
            {"tenant_id": str(context.tenant_id), "workflow_id": str(context.workflow_id), "group_key": group_key, "mid": message_id}
        )
        if exists:
            return ActivityResult(
                status=ActivityStatus.COMPLETED,
                output_data={
                    "message": "Duplicate message ignored for this batch (already queued)",
                    "group_key": group_key,
                    "deduped": True
                }
            )

    # Queue the row
    await execute_dict(
        """
        INSERT INTO csv_batch_rows (tenant_id, workflow_id, group_key, headers, row, message_id, execution_id)
        VALUES (:tenant_id, :workflow_id, :group_key, :headers, :row, :mid, :eid)
        """,
        {
            "tenant_id": str(context.tenant_id),
            "workflow_id": str(context.workflow_id),
            "group_key": group_key,
            "headers": _json.dumps(headers),
            "row": _json.dumps(row),
            "mid": message_id,
            "eid": str(context.execution_id)
        }
    )

    # Stats for this group
    stats = await fetch_one_dict(
        """
        SELECT COUNT(*)::int AS cnt, MIN(created_at) AS oldest
        FROM csv_batch_rows
        WHERE tenant_id = :tenant_id AND workflow_id = :workflow_id AND group_key = :group_key AND flushed_at IS NULL
        """,
        {"tenant_id": str(context.tenant_id), "workflow_id": str(context.workflow_id), "group_key": group_key}
    )
    cnt = (stats or {}).get("cnt", 0)
    oldest = (stats or {}).get("oldest")

    # Decide flush
    should_flush = False
    reasons = []
    if max_rows and cnt >= max_rows:
        should_flush = True
        reasons.append(f"row_threshold:{cnt}")
    if max_age_seconds and oldest:
        try:
            # oldest may already be a datetime object from asyncpg
            if isinstance(oldest, str):
                from datetime import datetime as _dt
                oldest_dt = _dt.fromisoformat(oldest)
            else:
                oldest_dt = oldest
            if datetime.utcnow() - oldest_dt >= timedelta(seconds=max_age_seconds):
                should_flush = True
                reasons.append("age_threshold")
        except Exception:
            pass

    # Rough size check: estimate bytes of CSV
    if max_bytes:
        # Quick estimate: average row JSON length as proxy
        avg_row_size = len(_json.dumps(row)) + 1
        est_bytes = avg_row_size * cnt + len(",".join(headers))
        if est_bytes >= max_bytes:
            should_flush = True
            reasons.append("size_threshold")

    if not should_flush:
        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "Row queued for batch",
                "group_key": group_key,
                "queued_rows": cnt
            },
            variables={
                "csv_batch_group": group_key,
                "csv_batch_count": cnt,
                "csv_batch_flushed": False
            }
        )

    # Fetch rows for flush
    rows = await fetch_all_dict(
        """
        SELECT id, headers, row FROM csv_batch_rows
        WHERE tenant_id = :tenant_id AND workflow_id = :workflow_id AND group_key = :group_key AND flushed_at IS NULL
        ORDER BY created_at ASC
        """,
        {"tenant_id": str(context.tenant_id), "workflow_id": str(context.workflow_id), "group_key": group_key}
    )

    if not rows:
        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={"message": "No rows to flush", "group_key": group_key}
        )

    # Determine header order
    header_list = headers if isinstance(headers, list) else rows[0].get("headers") or headers
    if isinstance(header_list, str):
        try:
            import json as _j
            header_list = _j.loads(header_list)
        except Exception:
            header_list = headers if isinstance(headers, list) else []
    header_list = header_list or []

    # Build CSV
    import csv as _csv, io as _io
    output = _io.StringIO()
    writer = _csv.DictWriter(output, fieldnames=header_list)
    writer.writeheader()
    for r in rows:
        row_obj = r.get("row")
        if isinstance(row_obj, str):
            try:
                row_obj = _json.loads(row_obj)
            except Exception:
                row_obj = {}
        # Ensure only header keys; fill missing with ''
        clean = {h: row_obj.get(h, "") for h in header_list}
        writer.writerow(clean)
    csv_content = output.getvalue()

    # Resolve S3 bucket/key
    s3_cfg = cfg.get("s3", {}) or {}
    bucket = s3_cfg.get("bucket") or s3_service.bucket_name
    key_tpl = s3_cfg.get("key_template") or group_tpl
    # Avoid overwriting: append timestamp and count
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    base_key = substitute(key_tpl)
    if base_key.endswith('/'):
        base_key += 'batch.csv'
    if not base_key.endswith('.csv'):
        base_key += '.csv'
    key = base_key.replace('.csv', f'_{ts}_{len(rows)}.csv')

    # Upload
    try:
        upload = await s3_service.upload_content(
            bucket=bucket,
            key=key,
            content=csv_content,
            content_type='text/csv',
            metadata={
                "tenant_id": str(context.tenant_id),
                "workflow_id": str(context.workflow_id),
                "group_key": group_key,
                "row_count": str(len(rows))
            }
        )
    except Exception as e:
        return ActivityResult(status=ActivityStatus.FAILED, error_message=f"S3 upload failed: {e}")

    # Mark flushed
    await execute_dict(
        """
        UPDATE csv_batch_rows SET flushed_at = NOW(), flush_key = :key
        WHERE tenant_id = :tenant_id AND workflow_id = :workflow_id AND group_key = :group_key AND flushed_at IS NULL
        """,
        {"tenant_id": str(context.tenant_id), "workflow_id": str(context.workflow_id), "group_key": group_key, "key": key}
    )

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={
            "message": "Batch flushed to S3",
            "group_key": group_key,
            "s3_bucket": bucket,
            "s3_key": key,
            "row_count": len(rows)
        },
        variables={
            "csv_batch_group": group_key,
            "csv_batch_count": 0,
            "csv_batch_flushed": True,
            "csv_s3_key": key
        }
    )


async def process_format_converter_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process format converter activity (mock)"""
    config = activity.get("config", {})
    input_format = config.get("input_format", "HL7")
    output_format = config.get("output_format", "JSON")

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={"message": f"Converted from {input_format} to {output_format}", "converted_data": "{}"}
    )


async def process_data_mapper_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process data mapper activity (mock)"""
    config = activity.get("config", {})
    mappings = config.get("mappings", [])

    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={"message": "Data mapping completed", "mappings_applied": len(mappings)}
    )


async def process_json_converter_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process JSON converter activity (mock)"""
    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={"message": "JSON conversion completed", "json_content": "{}"}
    )


async def process_xml_converter_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process XML converter activity (mock)"""
    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={"message": "XML conversion completed", "xml_content": "<root></root>"}
    )


async def process_pipe_converter_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process pipe-separated converter activity (mock)"""
    return ActivityResult(
        status=ActivityStatus.COMPLETED,
        output_data={"message": "Pipe-separated conversion completed", "pipe_content": ""}
    )
