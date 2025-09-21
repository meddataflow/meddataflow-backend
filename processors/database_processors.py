"""
Database Activity Processors
Handles various database operations for workflow activities including VPN-enabled connections
"""
import asyncio
import subprocess
import tempfile
import os
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import logging

# Database libraries - imported in functions as needed
# import asyncpg      # PostgreSQL async driver
# import aiomysql     # MySQL async driver
# import aioodbc      # SQL Server async driver
# import aiosqlite    # SQLite async driver

# Import models
from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus

logger = logging.getLogger(__name__)


@dataclass
class VPNConfig:
    """VPN configuration for database connections"""
    enabled: bool = False
    vpn_type: str = "openvpn"  # openvpn, wireguard, cisco_anyconnect
    config_file: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    server: Optional[str] = None
    port: Optional[int] = None
    ca_cert: Optional[str] = None
    client_cert: Optional[str] = None
    client_key: Optional[str] = None


class VPNManager:
    """Manages VPN connections for database access"""

    def __init__(self):
        self.active_connections = {}

    async def connect_vpn(self, tenant_id: str, vpn_config: VPNConfig) -> bool:
        """Establish VPN connection for tenant"""
        try:
            if not vpn_config.enabled:
                return True

            logger.info(f"Establishing VPN connection for tenant {tenant_id}")

            if vpn_config.vpn_type == "openvpn":
                return await self._connect_openvpn(tenant_id, vpn_config)
            elif vpn_config.vpn_type == "wireguard":
                return await self._connect_wireguard(tenant_id, vpn_config)
            elif vpn_config.vpn_type == "cisco_anyconnect":
                return await self._connect_cisco_anyconnect(tenant_id, vpn_config)
            else:
                logger.error(f"Unsupported VPN type: {vpn_config.vpn_type}")
                return False

        except Exception as e:
            logger.error(f"VPN connection failed for tenant {tenant_id}: {e}")
            return False

    async def disconnect_vpn(self, tenant_id: str):
        """Disconnect VPN for tenant"""
        if tenant_id in self.active_connections:
            try:
                process = self.active_connections[tenant_id]["process"]
                process.terminate()
                await asyncio.sleep(2)
                if process.poll() is None:
                    process.kill()
                del self.active_connections[tenant_id]
                logger.info(f"VPN disconnected for tenant {tenant_id}")
            except Exception as e:
                logger.error(f"Error disconnecting VPN for tenant {tenant_id}: {e}")

    async def _connect_openvpn(self, tenant_id: str, vpn_config: VPNConfig) -> bool:
        """Connect using OpenVPN"""
        try:
            # Create temporary config file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.ovpn', delete=False) as f:
                if vpn_config.config_file:
                    f.write(vpn_config.config_file)
                else:
                    # Build basic config
                    config_content = f"""
client
dev tun
proto udp
remote {vpn_config.server} {vpn_config.port or 1194}
resolv-retry infinite
nobind
persist-key
persist-tun
auth-user-pass
verb 3
"""
                    if vpn_config.ca_cert:
                        config_content += f"<ca>\n{vpn_config.ca_cert}\n</ca>\n"
                    if vpn_config.client_cert:
                        config_content += f"<cert>\n{vpn_config.client_cert}\n</cert>\n"
                    if vpn_config.client_key:
                        config_content += f"<key>\n{vpn_config.client_key}\n</key>\n"

                    f.write(config_content)
                config_file_path = f.name

            # Create auth file if credentials provided
            auth_file_path = None
            if vpn_config.username and vpn_config.password:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.auth', delete=False) as f:
                    f.write(f"{vpn_config.username}\n{vpn_config.password}\n")
                    auth_file_path = f.name

            # Start OpenVPN process
            cmd = ["openvpn", "--config", config_file_path]
            if auth_file_path:
                cmd.extend(["--auth-user-pass", auth_file_path])

            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Wait for connection establishment
            await asyncio.sleep(5)

            if process.poll() is None:
                self.active_connections[tenant_id] = {
                    "process": process,
                    "config_file": config_file_path,
                    "auth_file": auth_file_path,
                    "type": "openvpn"
                }
                logger.info(f"OpenVPN connected for tenant {tenant_id}")
                return True
            else:
                # Clean up files
                os.unlink(config_file_path)
                if auth_file_path:
                    os.unlink(auth_file_path)
                return False

        except Exception as e:
            logger.error(f"OpenVPN connection failed: {e}")
            return False

    async def _connect_wireguard(self, tenant_id: str, vpn_config: VPNConfig) -> bool:
        """Connect using WireGuard"""
        try:
            # Create temporary config file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
                f.write(vpn_config.config_file or "")
                config_file_path = f.name

            # Start WireGuard
            interface_name = f"wg-{tenant_id[:8]}"
            cmd = ["wg-quick", "up", config_file_path]

            process = subprocess.run(cmd, capture_output=True, text=True)

            if process.returncode == 0:
                self.active_connections[tenant_id] = {
                    "interface": interface_name,
                    "config_file": config_file_path,
                    "type": "wireguard"
                }
                logger.info(f"WireGuard connected for tenant {tenant_id}")
                return True
            else:
                os.unlink(config_file_path)
                return False

        except Exception as e:
            logger.error(f"WireGuard connection failed: {e}")
            return False

    async def _connect_cisco_anyconnect(self, tenant_id: str, vpn_config: VPNConfig) -> bool:
        """Connect using Cisco AnyConnect"""
        try:
            # Use expect script for AnyConnect automation
            expect_script = f"""#!/usr/bin/expect
spawn /opt/cisco/anyconnect/bin/vpn connect {vpn_config.server}
expect "Username:"
send "{vpn_config.username}\\r"
expect "Password:"
send "{vpn_config.password}\\r"
expect "VPN>"
exit 0
"""
            with tempfile.NamedTemporaryFile(mode='w', suffix='.exp', delete=False) as f:
                f.write(expect_script)
                script_path = f.name

            process = subprocess.run(["expect", script_path], capture_output=True, text=True)

            if "Connected" in process.stdout:
                self.active_connections[tenant_id] = {
                    "script_file": script_path,
                    "type": "cisco_anyconnect"
                }
                logger.info(f"Cisco AnyConnect connected for tenant {tenant_id}")
                return True
            else:
                os.unlink(script_path)
                return False

        except Exception as e:
            logger.error(f"Cisco AnyConnect connection failed: {e}")
            return False


# Global VPN manager instance
vpn_manager = VPNManager()


async def process_database_write_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """Process Database Write activity with support for multiple database types and VPN connections"""
    import asyncio

    config = activity.get("config", {})
    db_type = config.get("database_type", "postgresql")
    connection_config = config.get("connection", {})
    query_config = config.get("query_config", {})
    vpn_config_data = config.get("vpn", {})

    # Connection parameters
    host = connection_config.get("host", "localhost")
    port = connection_config.get("port", 5432 if db_type == "postgresql" else 3306)
    database = connection_config.get("database", "")
    username = connection_config.get("username", "")
    password = connection_config.get("password", "")

    # VPN configuration
    vpn_config = VPNConfig(
        enabled=vpn_config_data.get("enabled", False),
        vpn_type=vpn_config_data.get("vpn_type", "openvpn"),
        config_file=vpn_config_data.get("config_file"),
        username=vpn_config_data.get("username"),
        password=vpn_config_data.get("password"),
        server=vpn_config_data.get("server"),
        port=vpn_config_data.get("port"),
        ca_cert=vpn_config_data.get("ca_cert"),
        client_cert=vpn_config_data.get("client_cert"),
        client_key=vpn_config_data.get("client_key")
    )

    # Establish VPN connection if enabled
    vpn_connected = False
    if vpn_config.enabled:
        logger.info(f"VPN enabled for database connection, tenant: {context.tenant_id}")
        vpn_connected = await vpn_manager.connect_vpn(context.tenant_id, vpn_config)
        if not vpn_connected:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="Failed to establish VPN connection"
            )

    # Query parameters - check both locations for backward compatibility
    query = query_config.get("query", config.get("query", ""))
    query_type = query_config.get("query_type", "insert")
    table_name = query_config.get("table_name", "")

    # Extract parameters for parameterized queries (SQL injection protection)
    query_parameters = []
    substituted_query = query

    # Find all placeholders in the query and build parameters list
    import re
    placeholders = re.findall(r'\{(\w+)\}', query)

    for var_name in placeholders:
        if var_name in context.variables:
            query_parameters.append(context.variables[var_name])
        else:
            query_parameters.append(None)  # Default for missing variables

    # Convert placeholders to database-specific parameter markers
    if db_type == "postgresql":
        # PostgreSQL uses $1, $2, $3... for parameters
        for i, var_name in enumerate(placeholders, 1):
            single_placeholder = f"{{{var_name}}}"
            substituted_query = substituted_query.replace(single_placeholder, f"${i}")
    elif db_type == "mysql":
        # MySQL uses %s for parameters
        for var_name in placeholders:
            single_placeholder = f"{{{var_name}}}"
            substituted_query = substituted_query.replace(single_placeholder, "%s")
    elif db_type == "sqlite":
        # SQLite uses ? for parameters
        for var_name in placeholders:
            single_placeholder = f"{{{var_name}}}"
            substituted_query = substituted_query.replace(single_placeholder, "?")
    elif db_type == "sqlserver":
        # SQL Server uses ? for parameters
        for var_name in placeholders:
            single_placeholder = f"{{{var_name}}}"
            substituted_query = substituted_query.replace(single_placeholder, "?")

    try:
        # Database type specific connection handling
        if db_type == "postgresql":
            result = await _execute_postgresql_query(
                host, port, database, username, password, substituted_query, query_parameters, context
            )
        elif db_type == "mysql":
            result = await _execute_mysql_query(
                host, port, database, username, password, substituted_query, query_parameters, context
            )
        elif db_type == "sqlserver":
            result = await _execute_sqlserver_query(
                host, port, database, username, password, substituted_query, query_parameters, context
            )
        elif db_type == "sqlite":
            result = await _execute_sqlite_query(
                database, substituted_query, query_parameters, context
            )
        else:
            result = ActivityResult(
                status=ActivityStatus.FAILED,
                error_message=f"Unsupported database type: {db_type}"
            )

        # Add VPN info to result if connected
        if vpn_connected and result.output_data:
            result.output_data["vpn_connected"] = True
            result.output_data["vpn_type"] = vpn_config.vpn_type

        return result

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"Database write failed: {str(e)}",
            output_data={"query": substituted_query, "error": str(e), "vpn_connected": vpn_connected}
        )
    finally:
        # Clean up VPN connection if it was established
        if vpn_connected:
            await vpn_manager.disconnect_vpn(context.tenant_id)


async def _execute_postgresql_query(host: str, port: int, database: str, username: str, password: str, query: str, parameters: list, context: WorkflowContext) -> ActivityResult:
    """Execute PostgreSQL query"""
    try:
        import asyncpg

        connection_string = f"postgresql://{username}:{password}@{host}:{port}/{database}"
        conn = await asyncpg.connect(connection_string)

        if query.strip().upper().startswith(('SELECT', 'WITH')):
            # Query operation
            result = await conn.fetch(query, *parameters)
            rows_affected = len(result)
            result_data = [dict(row) for row in result]
        else:
            # Insert/Update/Delete operation
            result = await conn.execute(query, *parameters)
            rows_affected = int(result.split()[-1]) if result and result.split()[-1].isdigit() else 0
            result_data = []

        await conn.close()

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "PostgreSQL query executed successfully",
                "database_type": "postgresql",
                "query": query,
                "rows_affected": rows_affected,
                "result_data": result_data[:100]  # Limit to first 100 rows
            },
            variables={"db_rows_affected": rows_affected, "db_result_count": len(result_data)}
        )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"PostgreSQL execution failed: {str(e)}"
        )


async def _execute_mysql_query(host: str, port: int, database: str, username: str, password: str, query: str, parameters: list, context: WorkflowContext) -> ActivityResult:
    """Execute MySQL query"""
    try:
        import aiomysql

        conn = await aiomysql.connect(
            host=host, port=port, user=username, password=password, db=database
        )

        async with conn.cursor() as cursor:
            await cursor.execute(query, parameters)

            if query.strip().upper().startswith('SELECT'):
                result = await cursor.fetchall()
                rows_affected = len(result)
                result_data = [list(row) for row in result]
            else:
                await conn.commit()
                rows_affected = cursor.rowcount
                result_data = []

        conn.close()

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "MySQL query executed successfully",
                "database_type": "mysql",
                "query": query,
                "rows_affected": rows_affected,
                "result_data": result_data[:100]
            },
            variables={"db_rows_affected": rows_affected, "db_result_count": len(result_data)}
        )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"MySQL execution failed: {str(e)}"
        )


async def _execute_sqlserver_query(host: str, port: int, database: str, username: str, password: str, query: str, parameters: list, context: WorkflowContext) -> ActivityResult:
    """Execute SQL Server query"""
    try:
        import aioodbc

        connection_string = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={host},{port};DATABASE={database};UID={username};PWD={password}"
        conn = await aioodbc.connect(dsn=connection_string)

        async with conn.cursor() as cursor:
            await cursor.execute(query, parameters)

            if query.strip().upper().startswith('SELECT'):
                result = await cursor.fetchall()
                rows_affected = len(result)
                result_data = [list(row) for row in result]
            else:
                await conn.commit()
                rows_affected = cursor.rowcount
                result_data = []

        await conn.close()

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "SQL Server query executed successfully",
                "database_type": "sqlserver",
                "query": query,
                "rows_affected": rows_affected,
                "result_data": result_data[:100]
            },
            variables={"db_rows_affected": rows_affected, "db_result_count": len(result_data)}
        )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"SQL Server execution failed: {str(e)}"
        )


async def _execute_sqlite_query(database_path: str, query: str, parameters: list, context: WorkflowContext) -> ActivityResult:
    """Execute SQLite query"""
    try:
        import aiosqlite

        async with aiosqlite.connect(database_path) as conn:
            cursor = await conn.cursor()
            await cursor.execute(query, parameters)

            if query.strip().upper().startswith('SELECT'):
                result = await cursor.fetchall()
                rows_affected = len(result)
                result_data = [list(row) for row in result]
            else:
                await conn.commit()
                rows_affected = cursor.rowcount
                result_data = []

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "message": "SQLite query executed successfully",
                "database_type": "sqlite",
                "query": query,
                "rows_affected": rows_affected,
                "result_data": result_data[:100]
            },
            variables={"db_rows_affected": rows_affected, "db_result_count": len(result_data)}
        )

    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=f"SQLite execution failed: {str(e)}"
        )