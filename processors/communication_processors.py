"""
Communication activity processors for workflow execution
Contains HTTP, email, and Databricks communication processors extracted from workflow_execution_service.py
"""
import asyncio
import json
import httpx
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any
from datetime import datetime

from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus


async def process_http_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process HTTP sender activity with full functionality and variable selection"""
    import httpx
    import json

    config = activity.get("config", {})
    request_config = config.get("request", {})
    data_config = config.get("data", {})

    # HTTP request settings
    url = request_config.get("url", "")
    method = request_config.get("method", "POST").upper()
    # Support headers in both request.headers and top-level config.headers
    headers = request_config.get("headers", {}) or config.get("headers", {})
    timeout = request_config.get("timeout", 30)
    # Support auth in both request.auth and top-level config.auth
    auth_type = request_config.get("auth_type") or config.get("auth", {}).get("type", "none")
    auth_config = request_config.get("auth", {}) or config.get("auth", {})

    # Data settings
    # Support variable selection at top-level config.variables as well
    selected_variables = data_config.get("selected_variables") or config.get("variables", [])
    payload_format = data_config.get("format", "json")  # json, form, text, xml
    # Support custom payload at both data.custom_payload and top-level
    custom_payload = data_config.get("custom_payload", "") or config.get("custom_payload", "")

    if not url:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="HTTP URL is required"
        )

    # Prepare payload from selected variables
    payload_data = {}
    if selected_variables:
        for var_name in selected_variables:
            if var_name in context.variables:
                payload_data[var_name] = context.variables[var_name]
    else:
        # If no variables selected, send all variables
        payload_data = context.variables.copy()

    # Add metadata
    payload_data["workflow_execution_id"] = context.execution_id
    payload_data["timestamp"] = datetime.utcnow().isoformat()

    # Prepare payload based on format
    payload = None
    content_type = "application/json"

    if custom_payload:
        # Use custom payload with variable substitution
        payload = custom_payload
        for var_name, var_value in context.variables.items():
            placeholder = f"{{{{{var_name}}}}}"
            payload = payload.replace(placeholder, str(var_value))
        content_type = "text/plain"
    elif payload_format == "json":
        payload = json.dumps(payload_data)
        content_type = "application/json"
    elif payload_format == "form":
        payload = payload_data
        content_type = "application/x-www-form-urlencoded"
    elif payload_format == "xml":
        # Simple XML generation
        xml_items = []
        for key, value in payload_data.items():
            xml_items.append(f"<{key}>{value}</{key}>")
        payload = f"<data>{''.join(xml_items)}</data>"
        content_type = "application/xml"
    else:
        # Text format
        payload_lines = []
        for key, value in payload_data.items():
            payload_lines.append(f"{key}={value}")
        payload = "\n".join(payload_lines)
        content_type = "text/plain"

    # Set up headers
    request_headers = {"Content-Type": content_type}
    request_headers.update(headers)

    # Variable substitution in headers
    for header_name, header_value in request_headers.items():
        for var_name, var_value in context.variables.items():
            # Handle both single {var} and double {{var}} curly braces
            single_placeholder = f"{{{var_name}}}"
            double_placeholder = f"{{{{{var_name}}}}}"

            header_str = str(header_value)
            if single_placeholder in header_str:
                header_str = header_str.replace(single_placeholder, str(var_value))
            if double_placeholder in header_str:
                header_str = header_str.replace(double_placeholder, str(var_value))
            request_headers[header_name] = header_str

    # Set up authentication
    auth = None
    if auth_type == "basic":
        username = auth_config.get("username", "")
        password = auth_config.get("password", "")
        auth = (username, password)
    elif auth_type == "bearer":
        token = auth_config.get("token", "")
        request_headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                response = await client.get(url, headers=request_headers, auth=auth)
                response_text = response.text
                response_status = response.status_code
            elif method == "POST":
                response = await client.post(url, data=payload, headers=request_headers, auth=auth)
                response_text = response.text
                response_status = response.status_code
            elif method == "PUT":
                response = await client.put(url, data=payload, headers=request_headers, auth=auth)
                response_text = response.text
                response_status = response.status_code
            elif method == "DELETE":
                response = await client.delete(url, headers=request_headers, auth=auth)
                response_text = response.text
                response_status = response.status_code
            else:
                return ActivityResult(
                    status=ActivityStatus.FAILED,
                    error_message=f"Unsupported HTTP method: {method}"
                )

            # Determine if request was successful
            is_success = 200 <= response_status < 300

            return ActivityResult(
                status=ActivityStatus.COMPLETED if is_success else ActivityStatus.FAILED,
                output_data={
                    "message": f"HTTP {method} request completed",
                    "url": url,
                    "method": method,
                    "status_code": response_status,
                    "response_text": response_text[:1000],  # Limit response text
                    "payload_format": payload_format,
                    "variables_sent": list(payload_data.keys()) if payload_data else [],
                    "success": is_success
                },
                variables={
                    "http_status_code": response_status,
                    "http_success": is_success,
                    "http_response": response_text[:500]  # Store limited response
                },
                error_message=f"HTTP request failed with status {response_status}" if not is_success else None
            )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"HTTP request failed: {str(e)}"
        )


async def process_email_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process email sender activity with full SMTP functionality"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    config = activity.get("config", {})
    smtp_config = config.get("smtp", {})
    email_config = config.get("email", {})

    # SMTP settings
    smtp_server = smtp_config.get("host", smtp_config.get("server", "smtp.gmail.com"))  # Support both 'host' and 'server'
    smtp_port = smtp_config.get("port", 587)
    smtp_username = smtp_config.get("username", "")
    smtp_password = smtp_config.get("password", "")
    use_tls = smtp_config.get("use_tls", True)

    # Email settings
    from_email = email_config.get("from_email", smtp_username)
    to_emails = email_config.get("to_emails", [])

    # Handle to_emails as string or list
    if isinstance(to_emails, str):
        to_emails = [email.strip() for email in to_emails.split(",")]
    subject = email_config.get("subject", "HL7 Workflow Notification")
    body = email_config.get("body", "Workflow execution completed")

    # Variable substitution
    for var_name, var_value in context.variables.items():
        # Handle both single {var} and double {{var}} curly braces
        single_placeholder = f"{{{var_name}}}"
        double_placeholder = f"{{{{{var_name}}}}}"

        if single_placeholder in subject:
            subject = subject.replace(single_placeholder, str(var_value))
        if double_placeholder in subject:
            subject = subject.replace(double_placeholder, str(var_value))
        if single_placeholder in body:
            body = body.replace(single_placeholder, str(var_value))
        if double_placeholder in body:
            body = body.replace(double_placeholder, str(var_value))

    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = ", ".join(to_emails)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Send email
        server = smtplib.SMTP(smtp_server, smtp_port)
        if use_tls:
            server.starttls()
        server.login(smtp_username, smtp_password)
        text = msg.as_string()
        server.sendmail(from_email, to_emails, text)
        server.quit()

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "Email sent successfully",
                "recipients": len(to_emails),
                "subject": subject,
                "smtp_server": smtp_server
            },
            variables={"email_sent": True, "email_recipients": len(to_emails)}
        )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Email sending failed: {str(e)}"
        )


async def process_tcp_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process TCP sender activity - send raw/transformed HL7 over TCP.

    Config shape (frontend):
      {
        host: string,
        port: number,
        connection_timeout?: number,
        read_timeout?: number,
        use_ssl?: boolean
      }
    """
    import asyncio
    import ssl

    config = activity.get("config", {})
    host = config.get("host", "localhost")
    port = int(config.get("port", 1080))
    conn_timeout = float(config.get("connection_timeout", 5.0))
    read_timeout = float(config.get("read_timeout", 30.0))
    use_ssl = bool(config.get("use_ssl", False))
    use_mllp = bool(config.get("use_mllp", False))
    expect_ack = bool(config.get("expect_ack", False))
    ack_timeout = float(config.get("ack_timeout_ms", 5000)) / 1000.0

    message = context.raw_message or context.variables.get("message") or ""
    if not message:
        return ActivityResult(status=ActivityStatus.FAILED, error_message="No message available to send over TCP")

    try:
        ssl_ctx = None
        if use_ssl:
            ssl_ctx = ssl.create_default_context()

        connect_coro = asyncio.open_connection(host=host, port=port, ssl=ssl_ctx)
        reader, writer = await asyncio.wait_for(connect_coro, timeout=conn_timeout)

        # Send message: optionally MLLP framed
        if use_mllp:
            # MLLP: SB 0x0b, EB 0x1c, CR 0x0d
            data = b"\x0b" + message.encode("utf-8") + b"\x1c\x0d"
        else:
            data = (message + "\n").encode("utf-8")
        writer.write(data)
        await writer.drain()

        # Attempt to read response (best-effort)
        response_data = b""
        try:
            if expect_ack:
                # Wait for ACK (MLLP if use_mllp)
                timeout = ack_timeout if ack_timeout > 0 else read_timeout
                chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
                response_data = chunk or b""
            else:
                response_data = await asyncio.wait_for(reader.read(4096), timeout=read_timeout)
        except asyncio.TimeoutError:
            # No response within timeout; still consider as sent
            response_data = b""

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "TCP send completed",
                "host": host,
                "port": port,
                "bytes_sent": len(data),
                "response_preview": response_data[:200].decode("utf-8", errors="ignore") if response_data else "",
                "mllp": use_mllp,
                "expect_ack": expect_ack
            },
            variables={"tcp_sent": True, "tcp_host": host, "tcp_port": port}
        )
    except Exception as e:
        return ActivityResult(status=ActivityStatus.FAILED, error_message=f"TCP send failed: {str(e)}")

async def process_databricks_sender_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    """Process Databricks sender activity - send data to Databricks workspace

    Supports two config shapes:
    - Legacy: config.connection.workspace_url/access_token, config.data.database/target_table
    - New UI: config.connection.workspace_url/http_path/access_token/catalog/schema/table

    Will insert a single column 'message' value into the target table, using the
    transformed HL7 in context.raw_message (post-transform activity) or a provided override.
    """
    import json
    import httpx
    from databricks import sql as dbsql

    config = activity.get("config", {})
    connection_config = config.get("connection", {})
    data_config = config.get("data", {})
    sql_config = config.get("sql", {}) or {}
    execution_config = config.get("execution", {}) or {}

    # Connection settings
    workspace_url = (connection_config.get("workspace_url") or connection_config.get("host") or "").rstrip('/')
    access_token = connection_config.get("access_token", "")
    http_path = connection_config.get("http_path", "")
    catalog = connection_config.get("catalog")
    schema = connection_config.get("schema")
    table = connection_config.get("table")

    # Legacy fallback
    database_name = data_config.get("database")
    target_table = data_config.get("target_table")

    # Determine full table identifier
    full_table = None
    if table and schema:
        parts = [p for p in [catalog, schema, table] if p]
        full_table = ".".join(parts)
    elif database_name and target_table:
        full_table = f"{database_name}.{target_table}"

    if not workspace_url or not access_token:
        return ActivityResult(status=ActivityStatus.FAILED, error_message="Databricks workspace_url and access_token are required")
    if not full_table:
        return ActivityResult(status=ActivityStatus.FAILED, error_message="Target table is required (set connection.catalog/schema/table or data.database/target_table)")

    # Fail fast on clearly placeholder values
    if "your-workspace" in workspace_url or "<workspace>" in workspace_url:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="Invalid Databricks workspace_url (placeholder detected). Please set a real workspace hostname.",
            output_data={"workspace_url": workspace_url}
        )
    if http_path and ("your-warehouse-id" in http_path or "<id>" in http_path):
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="Invalid Databricks http_path (placeholder detected). Please set a real SQL warehouse http_path.",
            output_data={"http_path": http_path}
        )

    # Optional fail-fast DNS preflight to avoid long connector retries
    fail_fast = bool(execution_config.get("fail_fast", True))
    try:
        if fail_fast:
            import socket
            host = workspace_url.replace("https://", "").replace("http://", "").split("/")[0]
            # This will raise socket.gaierror quickly if DNS is invalid
            socket.getaddrinfo(host, None)
    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Databricks hostname resolution failed: {e}",
            output_data={"workspace_url": workspace_url}
        )

    # Message to insert: prefer the current raw_message (updated by transformer)
    message_value = context.raw_message or context.variables.get("message")
    if not message_value:
        return ActivityResult(status=ActivityStatus.FAILED, error_message="No HL7 message found to send")

    # Optional pre/post SQL
    pre_sql = sql_config.get("pre_insert")
    post_sql = sql_config.get("post_insert")

    # Ensure http_path is available; if not, try to discover a warehouse and build it
    async def ensure_http_path() -> str:
        nonlocal http_path
        if http_path:
            return http_path
        # Discover a SQL warehouse via REST API
        warehouses_url = f"{workspace_url}/api/2.0/sql/warehouses"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(warehouses_url, headers=headers)
                if resp.status_code // 100 != 2:
                    return ""
                data = resp.json() or {}
                warehouses = data.get("warehouses", [])
                if not warehouses:
                    return ""
                # Pick the first RUNNING/ENABLED one, else first
                chosen = None
                for wh in warehouses:
                    state = (wh.get("state") or "").upper()
                    if state in ("RUNNING", "ONLINE", "ENABLED"):
                        chosen = wh
                        break
                if not chosen:
                    chosen = warehouses[0]
                wid = chosen.get("id") or chosen.get("warehouse_id")
                if wid:
                    http_path = f"/sql/1.0/warehouses/{wid}"
                    return http_path
        except Exception:
            return ""
        return ""

    try:
        # Guarantee http_path if possible
        if not http_path:
            await ensure_http_path()

        # Build SQL statements
        insert_sql = f"INSERT INTO {full_table} VALUES (?)"

        # Execute using Databricks SQL Connector (preferred when http_path available)
        if http_path:
            try:
                with dbsql.connect(server_hostname=workspace_url.replace("https://", "").replace("http://", ""),
                                   http_path=http_path,
                                   access_token=access_token) as conn:
                    with conn.cursor() as cur:
                        if pre_sql:
                            cur.execute(pre_sql)
                        cur.execute(insert_sql, (message_value,))
                        if post_sql:
                            cur.execute(post_sql)

                return ActivityResult(
                    status=ActivityStatus.COMPLETED,
                    output_data={
                        "message": "Data sent to Databricks successfully",
                        "target_table": full_table,
                        "records_sent": 1,
                        "used_http_path": http_path
                    },
                    variables={
                        "databricks_sent": True,
                        "databricks_records": 1,
                        "databricks_table": full_table
                    }
                )
            except Exception as e:
                # If fail-fast, do not keep trying with connector internal retries
                if fail_fast:
                    return ActivityResult(
                        status=ActivityStatus.FAILED,
                        error_message=f"Databricks SQL connector failed: {e}",
                        output_data={"workspace_url": workspace_url, "http_path": http_path}
                    )
                # else: fall back to REST API for statement execution

        # REST fallback: use Statements Execute endpoint if available
        api_url = f"{workspace_url}/api/2.0/sql/statements/execute"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        statements: list[str] = []
        if pre_sql:
            statements.append(pre_sql)
        # Escape not needed with parameterization, but REST execute may not support parameters in same way
        # Use literal escaping for safety
        safe_message = message_value.replace("'", "''")
        statements.append(f"INSERT INTO {full_table} VALUES ('{safe_message}')")
        if post_sql:
            statements.append(post_sql)

        payload = {
            "statement": "\n;\n".join(statements),
        }
        # If we discovered/know a warehouse id, prefer older endpoint
        if not http_path:
            # Try to pick a warehouse via warehouses API to include in payload
            wh_url = f"{workspace_url}/api/2.0/sql/warehouses"
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.get(wh_url, headers=headers)
                    if r.status_code // 100 == 2:
                        wid = (r.json().get("warehouses") or [{}])[0].get("id")
                        if wid:
                            payload["warehouse_id"] = wid
            except Exception:
                pass

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(api_url, headers=headers, json=payload)
            if resp.status_code // 100 == 2:
                return ActivityResult(
                    status=ActivityStatus.COMPLETED,
                    output_data={
                        "message": "Data sent to Databricks successfully (REST)",
                        "target_table": full_table,
                        "records_sent": 1
                    },
                    variables={
                        "databricks_sent": True,
                        "databricks_records": 1,
                        "databricks_table": full_table
                    }
                )
            else:
                return ActivityResult(
                    status=ActivityStatus.FAILED,
                    error_message=f"Databricks API error: HTTP {resp.status_code}"
                )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Databricks sending failed: {str(e)}"
        )


async def process_sqs_producer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    from services.secrets import resolve_secret
    cfg = activity.get('config', {}) or {}
    queue_url = cfg.get('queue_url')
    queue_name = cfg.get('queue_name')
    region = cfg.get('region') or (cfg.get('aws') or {}).get('region') or 'us-east-1'
    body_template = cfg.get('body_template') or ''
    attributes_cfg = cfg.get('attributes') or {}
    aws = cfg.get('aws') or {}
    ak = await resolve_secret(aws.get('access_key_id'))
    sk = await resolve_secret(aws.get('secret_access_key'))

    try:
        import boto3
    except Exception as e:
        return ActivityResult(status=ActivityStatus.FAILED, error_message=f"boto3 unavailable: {e}")

    def render(tpl: str) -> str:
        out = tpl or ''
        for k, v in context.variables.items():
            out = out.replace(f"{{{k}}}", str(v)).replace(f"{{{{{k}}}}}", str(v))
        return out

    session_kwargs = { 'region_name': region }
    if ak and sk:
        session_kwargs.update({ 'aws_access_key_id': ak, 'aws_secret_access_key': sk })
    sqs = boto3.client('sqs', **session_kwargs)

    if not queue_url and queue_name:
        try:
            q = sqs.get_queue_url(QueueName=queue_name)
            queue_url = q.get('QueueUrl')
        except Exception as e:
            return ActivityResult(status=ActivityStatus.FAILED, error_message=f"Failed to resolve queue: {e}")
    if not queue_url:
        return ActivityResult(status=ActivityStatus.FAILED, error_message="No queue_url or queue_name provided")

    body = render(body_template)
    message_attributes = { k: { 'DataType': 'String', 'StringValue': render(str(v)) } for k, v in attributes_cfg.items() }
    try:
        resp = sqs.send_message(QueueUrl=queue_url, MessageBody=body, MessageAttributes=message_attributes)
        mid = resp.get('MessageId')
        return ActivityResult(status=ActivityStatus.COMPLETED, output_data={ 'message_id': mid, 'queue_url': queue_url }, variables={ 'sqs_message_id': mid })
    except Exception as e:
        return ActivityResult(status=ActivityStatus.FAILED, error_message=f"SQS send failed: {e}")


async def process_sqs_consumer_activity(activity: Dict[str, Any], context: WorkflowContext) -> ActivityResult:
    from services.secrets import resolve_secret
    cfg = activity.get('config', {}) or {}
    queue_url = cfg.get('queue_url')
    queue_name = cfg.get('queue_name')
    region = cfg.get('region') or (cfg.get('aws') or {}).get('region') or 'us-east-1'
    max_messages = int(cfg.get('max_messages') or 1)
    wait_seconds = int(cfg.get('wait_seconds') or 0)
    delete_after = bool(cfg.get('delete_after'))
    aws = cfg.get('aws') or {}
    ak = await resolve_secret(aws.get('access_key_id'))
    sk = await resolve_secret(aws.get('secret_access_key'))

    try:
        import boto3
    except Exception as e:
        return ActivityResult(status=ActivityStatus.FAILED, error_message=f"boto3 unavailable: {e}")

    session_kwargs = { 'region_name': region }
    if ak and sk:
        session_kwargs.update({ 'aws_access_key_id': ak, 'aws_secret_access_key': sk })
    sqs = boto3.client('sqs', **session_kwargs)

    if not queue_url and queue_name:
        try:
            q = sqs.get_queue_url(QueueName=queue_name)
            queue_url = q.get('QueueUrl')
        except Exception as e:
            return ActivityResult(status=ActivityStatus.FAILED, error_message=f"Failed to resolve queue: {e}")
    if not queue_url:
        return ActivityResult(status=ActivityStatus.FAILED, error_message="No queue_url or queue_name provided")

    try:
        resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=max_messages, WaitTimeSeconds=wait_seconds, MessageAttributeNames=['All'])
        messages = resp.get('Messages') or []
        out = []
        for m in messages:
            body = m.get('Body')
            attrs = m.get('MessageAttributes') or {}
            out.append({ 'id': m.get('MessageId'), 'body': body, 'attributes': attrs })
            if delete_after and m.get('ReceiptHandle'):
                try:
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m['ReceiptHandle'])
                except Exception:
                    pass
        variables = { 'sqs_messages': out }
        if out and not context.raw_message:
            variables['message'] = out[0]['body']
        return ActivityResult(status=ActivityStatus.COMPLETED, output_data={ 'received': len(out) }, variables=variables)
    except Exception as e:
        return ActivityResult(status=ActivityStatus.FAILED, error_message=f"SQS receive failed: {e}")
