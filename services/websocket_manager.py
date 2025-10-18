"""
WebSocket Manager Service for Real-Time Workflow Execution Updates
Manages WebSocket connections and broadcasts workflow execution events
"""
import asyncio
import json
import logging
from typing import Dict, Set, Any, Optional
from fastapi import WebSocket
from datetime import datetime

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for real-time updates"""

    def __init__(self):
        # Store active connections by execution_id
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # Store connections by user/tenant for broadcast capabilities
        self.tenant_connections: Dict[str, Set[WebSocket]] = {}
        # Store metadata for each connection
        self.connection_metadata: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(
        self,
        websocket: WebSocket,
        execution_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None
    ):
        """Accept and register a new WebSocket connection"""
        await websocket.accept()

        # Store metadata
        self.connection_metadata[websocket] = {
            "execution_id": execution_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "connected_at": datetime.utcnow().isoformat()
        }

        # Register by execution_id if provided
        if execution_id:
            if execution_id not in self.active_connections:
                self.active_connections[execution_id] = set()
            self.active_connections[execution_id].add(websocket)
            logger.info(f"WebSocket connected for execution: {execution_id}")

        # Register by tenant_id for broadcast capabilities
        if tenant_id:
            if tenant_id not in self.tenant_connections:
                self.tenant_connections[tenant_id] = set()
            self.tenant_connections[tenant_id].add(websocket)
            logger.info(f"WebSocket connected for tenant: {tenant_id}")

    def disconnect(self, websocket: WebSocket):
        """Unregister a WebSocket connection"""
        metadata = self.connection_metadata.get(websocket, {})
        execution_id = metadata.get("execution_id")
        tenant_id = metadata.get("tenant_id")

        # Remove from execution connections
        if execution_id and execution_id in self.active_connections:
            self.active_connections[execution_id].discard(websocket)
            if not self.active_connections[execution_id]:
                del self.active_connections[execution_id]
            logger.info(f"WebSocket disconnected from execution: {execution_id}")

        # Remove from tenant connections
        if tenant_id and tenant_id in self.tenant_connections:
            self.tenant_connections[tenant_id].discard(websocket)
            if not self.tenant_connections[tenant_id]:
                del self.tenant_connections[tenant_id]

        # Remove metadata
        if websocket in self.connection_metadata:
            del self.connection_metadata[websocket]

    async def send_personal_message(self, message: Dict[str, Any], websocket: WebSocket):
        """Send a message to a specific connection"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")
            self.disconnect(websocket)

    async def broadcast_to_execution(self, execution_id: str, message: Dict[str, Any]):
        """Broadcast a message to all connections watching a specific execution"""
        if execution_id not in self.active_connections:
            logger.debug(f"No active connections for execution: {execution_id}")
            return

        connections = list(self.active_connections[execution_id])
        disconnected = []

        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to execution {execution_id}: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast_to_tenant(self, tenant_id: str, message: Dict[str, Any]):
        """Broadcast a message to all connections for a specific tenant"""
        if tenant_id not in self.tenant_connections:
            return

        connections = list(self.tenant_connections[tenant_id])
        disconnected = []

        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to tenant {tenant_id}: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def send_execution_started(
        self,
        execution_id: str,
        workflow_id: str,
        tenant_id: str,
        started_at: str
    ):
        """Send execution started event"""
        message = {
            "event": "execution_started",
            "execution_id": execution_id,
            "workflow_id": workflow_id,
            "started_at": started_at,
            "status": "RUNNING"
        }
        await self.broadcast_to_execution(execution_id, message)
        await self.broadcast_to_tenant(tenant_id, message)

    async def send_activity_started(
        self,
        execution_id: str,
        tenant_id: str,
        activity_name: str,
        activity_type: str,
        activity_index: int
    ):
        """Send activity started event"""
        message = {
            "event": "activity_started",
            "execution_id": execution_id,
            "activity_name": activity_name,
            "activity_type": activity_type,
            "activity_index": activity_index,
            "timestamp": datetime.utcnow().isoformat()
        }
        await self.broadcast_to_execution(execution_id, message)

    async def send_activity_completed(
        self,
        execution_id: str,
        tenant_id: str,
        activity_name: str,
        activity_type: str,
        activity_index: int,
        execution_time_ms: int,
        status: str,
        output_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None
    ):
        """Send activity completed event"""
        message = {
            "event": "activity_completed",
            "execution_id": execution_id,
            "activity_name": activity_name,
            "activity_type": activity_type,
            "activity_index": activity_index,
            "execution_time_ms": execution_time_ms,
            "status": status,
            "timestamp": datetime.utcnow().isoformat()
        }

        if output_data:
            message["output_data"] = output_data

        if error_message:
            message["error_message"] = error_message

        await self.broadcast_to_execution(execution_id, message)

    async def send_execution_completed(
        self,
        execution_id: str,
        tenant_id: str,
        status: str,
        execution_time_ms: int,
        activities_executed: int,
        activities_skipped: int,
        error_message: Optional[str] = None
    ):
        """Send execution completed event"""
        message = {
            "event": "execution_completed",
            "execution_id": execution_id,
            "status": status,
            "execution_time_ms": execution_time_ms,
            "activities_executed": activities_executed,
            "activities_skipped": activities_skipped,
            "completed_at": datetime.utcnow().isoformat()
        }

        if error_message:
            message["error_message"] = error_message

        await self.broadcast_to_execution(execution_id, message)
        await self.broadcast_to_tenant(tenant_id, message)

    async def send_execution_progress(
        self,
        execution_id: str,
        tenant_id: str,
        current_activity: int,
        total_activities: int,
        progress_percentage: float
    ):
        """Send execution progress update"""
        message = {
            "event": "execution_progress",
            "execution_id": execution_id,
            "current_activity": current_activity,
            "total_activities": total_activities,
            "progress_percentage": progress_percentage,
            "timestamp": datetime.utcnow().isoformat()
        }
        await self.broadcast_to_execution(execution_id, message)

    def get_active_connections_count(self, execution_id: Optional[str] = None) -> int:
        """Get count of active connections"""
        if execution_id:
            return len(self.active_connections.get(execution_id, set()))
        return sum(len(conns) for conns in self.active_connections.values())

    def get_connection_info(self) -> Dict[str, Any]:
        """Get information about all active connections"""
        return {
            "total_connections": sum(len(conns) for conns in self.active_connections.values()),
            "executions_tracked": len(self.active_connections),
            "tenants_connected": len(self.tenant_connections),
            "connections_by_execution": {
                exec_id: len(conns)
                for exec_id, conns in self.active_connections.items()
            }
        }


# Global WebSocket manager instance
websocket_manager = ConnectionManager()
