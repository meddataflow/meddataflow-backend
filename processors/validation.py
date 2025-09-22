"""
Input validation schemas for activity processors
Provides comprehensive validation to prevent injection attacks
"""
import re
from typing import Any, Dict, Optional
from marshmallow import Schema, fields, ValidationError, validates_schema, validate


class DatabaseConfigSchema(Schema):
    """Validation schema for database configuration"""
    database_type = fields.Str(
        required=True,
        validate=validate.OneOf(['postgresql', 'mysql', 'sqlite', 'sqlserver'])
    )

    connection = fields.Dict(required=True)
    query_config = fields.Dict(required=True)
    vpn = fields.Dict(load_default=dict)

    @validates_schema
    def validate_connection(self, data, **kwargs):
        """Validate connection parameters"""
        connection = data.get('connection', {})

        # Validate host
        host = connection.get('host', '')
        if host and not re.match(r'^[a-zA-Z0-9.-]+$', host):
            raise ValidationError("Invalid host format", field_name='connection.host')

        # Validate port
        port = connection.get('port')
        if port is not None:
            if not isinstance(port, int) or not (1 <= port <= 65535):
                raise ValidationError("Port must be between 1 and 65535", field_name='connection.port')

        # Validate database name
        database = connection.get('database', '')
        if database and not re.match(r'^[a-zA-Z0-9_-]+$', database):
            raise ValidationError("Invalid database name format", field_name='connection.database')

        # Validate username
        username = connection.get('username', '')
        if username and not re.match(r'^[a-zA-Z0-9._@-]+$', username):
            raise ValidationError("Invalid username format", field_name='connection.username')

    @validates_schema
    def validate_query(self, data, **kwargs):
        """Validate query parameters"""
        query_config = data.get('query_config', {})

        # Validate query length
        query = query_config.get('query', '')
        if len(query) > 50000:
            raise ValidationError("Query too long (max 50000 characters)", field_name='query_config.query')

        # Basic SQL injection prevention - check for dangerous patterns
        dangerous_patterns = [
            r';\s*(drop|delete|truncate|alter)\s+',
            r'union\s+.*select',
            r'exec\s*\(',
            r'xp_cmdshell',
            r'sp_executesql'
        ]

        query_lower = query.lower()
        for pattern in dangerous_patterns:
            if re.search(pattern, query_lower):
                raise ValidationError("Query contains potentially dangerous SQL patterns", field_name='query_config.query')


class VPNConfigSchema(Schema):
    """Validation schema for VPN configuration"""
    enabled = fields.Bool(load_default=False)
    vpn_type = fields.Str(
        validate=validate.OneOf(['openvpn', 'wireguard', 'cisco_anyconnect']),
        load_default='openvpn'
    )
    server = fields.Str(validate=validate.Regexp(r'^[a-zA-Z0-9.-]+$'))
    port = fields.Int(validate=validate.Range(min=1, max=65535))
    username = fields.Str(validate=validate.Regexp(r'^[a-zA-Z0-9._@-]+$'))
    password = fields.Str()
    config_file = fields.Str(validate=validate.Length(max=100000))
    ca_cert = fields.Str(validate=validate.Length(max=10000))
    client_cert = fields.Str(validate=validate.Length(max=10000))
    client_key = fields.Str(validate=validate.Length(max=10000))


class EmailConfigSchema(Schema):
    """Validation schema for email configuration"""
    smtp_server = fields.Str(
        required=True,
        validate=validate.Regexp(r'^[a-zA-Z0-9.-]+$')
    )
    smtp_port = fields.Int(
        required=True,
        validate=validate.Range(min=1, max=65535)
    )
    smtp_username = fields.Str(validate=validate.Email())
    smtp_password = fields.Str()
    use_tls = fields.Bool(load_default=True)
    use_ssl = fields.Bool(load_default=False)

    to_emails = fields.List(
        fields.Str(validate=validate.Email()),
        required=True,
        validate=validate.Length(min=1, max=100)
    )
    from_email = fields.Str(
        required=True,
        validate=validate.Email()
    )
    subject = fields.Str(
        required=True,
        validate=validate.Length(max=1000)
    )
    body = fields.Str(validate=validate.Length(max=1000000))


class HTTPConfigSchema(Schema):
    """Validation schema for HTTP configuration"""
    url = fields.Url(required=True)
    method = fields.Str(
        required=True,
        validate=validate.OneOf(['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
    )
    headers = fields.Dict(
        keys=fields.Str(validate=validate.Regexp(r'^[a-zA-Z0-9-_]+$')),
        values=fields.Str(validate=validate.Length(max=10000))
    )
    timeout = fields.Int(validate=validate.Range(min=1, max=300))
    retries = fields.Int(validate=validate.Range(min=0, max=10))


class FileConfigSchema(Schema):
    """Validation schema for file operations"""
    file_path = fields.Str(
        required=True,
        validate=validate.Length(max=1000)
    )

    @validates_schema
    def validate_file_path(self, data, **kwargs):
        """Validate file path for security"""
        file_path = data.get('file_path', '')

        # Prevent directory traversal
        if '..' in file_path:
            raise ValidationError("File path cannot contain '..'", field_name='file_path')

        # Prevent access to system files
        dangerous_paths = ['/etc/', '/var/', '/usr/', '/bin/', '/sbin/', 'C:\\Windows\\', 'C:\\Program Files\\']
        for dangerous in dangerous_paths:
            if file_path.startswith(dangerous):
                raise ValidationError("Access to system directories not allowed", field_name='file_path')


class CustomCodeConfigSchema(Schema):
    """Validation schema for custom code execution"""
    language = fields.Str(
        required=True,
        validate=validate.OneOf(['python', 'javascript', 'bash'])
    )
    code = fields.Str(
        required=True,
        validate=validate.Length(max=100000)
    )
    timeout = fields.Int(validate=validate.Range(min=1, max=300))

    @validates_schema
    def validate_code(self, data, **kwargs):
        """Validate code for dangerous patterns"""
        code = data.get('code', '').lower()
        language = data.get('language', '')

        # Common dangerous patterns across languages
        dangerous_patterns = [
            'import os',
            'import sys',
            'import subprocess',
            'exec(',
            'eval(',
            '__import__',
            'open(',
            'file(',
            'input(',
            'raw_input(',
            'rm -rf',
            'del /s',
            'format c:',
            'shutdown',
            'reboot'
        ]

        for pattern in dangerous_patterns:
            if pattern in code:
                raise ValidationError(f"Code contains dangerous pattern: {pattern}", field_name='code')

        # Language-specific validation
        if language == 'python':
            python_dangerous = ['__builtins__', '__globals__', '__locals__', 'compile(']
            for pattern in python_dangerous:
                if pattern in code:
                    raise ValidationError(f"Python code contains dangerous pattern: {pattern}", field_name='code')


def validate_activity_config(activity_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate activity configuration based on activity type

    Args:
        activity_type: Type of activity (database_write, email_send, etc.)
        config: Activity configuration to validate

    Returns:
        Validated and sanitized configuration

    Raises:
        ValidationError: If validation fails
    """
    schema_map = {
        'database_write': DatabaseConfigSchema(),
        'email_send': EmailConfigSchema(),
        'http_request': HTTPConfigSchema(),
        'file_write': FileConfigSchema(),
        'file_read': FileConfigSchema(),
        'custom_code': CustomCodeConfigSchema()
    }

    schema = schema_map.get(activity_type)
    if not schema:
        # For unknown activity types, perform basic validation
        if not isinstance(config, dict):
            raise ValidationError("Configuration must be a dictionary")
        return config

    try:
        return schema.load(config)
    except ValidationError as e:
        raise ValidationError(f"Configuration validation failed for {activity_type}: {e.messages}")


def sanitize_sql_query(query: str, query_type: str = 'select') -> str:
    """
    Sanitize SQL query by removing dangerous patterns

    Args:
        query: SQL query to sanitize
        query_type: Expected query type (select, insert, update, delete)

    Returns:
        Sanitized query
    """
    # Remove comments
    query = re.sub(r'--.*$', '', query, flags=re.MULTILINE)
    query = re.sub(r'/\*.*?\*/', '', query, flags=re.DOTALL)

    # Remove extra whitespace
    query = ' '.join(query.split())

    # Validate query starts with expected statement
    query_lower = query.lower().strip()
    expected_starts = {
        'select': ['select', 'with'],
        'insert': ['insert'],
        'update': ['update'],
        'delete': ['delete'],
        'upsert': ['insert', 'update', 'merge']
    }

    valid_starts = expected_starts.get(query_type, [])
    if valid_starts and not any(query_lower.startswith(start) for start in valid_starts):
        raise ValidationError(f"Query must start with {' or '.join(valid_starts)} for type {query_type}")

    return query


def validate_hostname(hostname: str) -> bool:
    """
    Validate hostname format

    Args:
        hostname: Hostname to validate

    Returns:
        True if valid, False otherwise
    """
    if not hostname:
        return False

    # Basic hostname validation
    hostname_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
    return bool(re.match(hostname_pattern, hostname))


def validate_email_address(email: str) -> bool:
    """
    Validate email address format

    Args:
        email: Email address to validate

    Returns:
        True if valid, False otherwise
    """
    if not email:
        return False

    # Basic email validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_pattern, email))