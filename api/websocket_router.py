"""
WebSocket Router for Real-Time Workflow Execution Updates
Provides WebSocket endpoints for subscribing to workflow execution events
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional
import logging
import json

from services.websocket_manager import websocket_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/execution/{execution_id}")
async def websocket_execution_endpoint(
    websocket: WebSocket,
    execution_id: str,
    token: Optional[str] = Query(None)
):
    """
    WebSocket endpoint for real-time workflow execution updates

    Connect to this endpoint to receive real-time updates for a specific workflow execution.

    **Query Parameters:**
    - token: JWT authentication token (optional, for authenticated connections)

    **Events Sent:**
    - execution_started: When workflow execution begins
    - activity_started: When an activity starts processing
    - activity_completed: When an activity completes (success or failure)
    - execution_progress: Progress percentage updates
    - execution_completed: When workflow execution completes

    **Example Usage:**
    ```javascript
    const ws = new WebSocket(`ws://localhost:8001/ws/execution/${executionId}?token=${authToken}`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log('Event:', data.event, data);
    };
    ```
    """
    tenant_id = None
    user_id = None

    # Optionally validate token if provided
    if token:
        try:
            # You can add token validation here
            # user_data = verify_token(token)
            # tenant_id = user_data.get("tenant_id")
            # user_id = user_data.get("user_id")
            pass
        except Exception as e:
            logger.warning(f"WebSocket token validation failed: {e}")

    await websocket_manager.connect(
        websocket,
        execution_id=execution_id,
        tenant_id=tenant_id,
        user_id=user_id
    )

    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "event": "connected",
            "execution_id": execution_id,
            "message": "Successfully connected to workflow execution updates"
        })

        # Keep connection alive and handle any incoming messages
        while True:
            try:
                data = await websocket.receive_text()

                # Handle ping/pong for keepalive
                if data == "ping":
                    await websocket.send_json({"event": "pong"})
                else:
                    # Parse and handle other client messages if needed
                    try:
                        message = json.loads(data)
                        # Handle client requests (e.g., pause, resume, cancel)
                        if message.get("action") == "status":
                            await websocket.send_json({
                                "event": "status_response",
                                "connections": websocket_manager.get_active_connections_count(execution_id)
                            })
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON received: {data}")

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error in WebSocket receive loop: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for execution: {execution_id}")
    except Exception as e:
        logger.error(f"WebSocket error for execution {execution_id}: {e}")
    finally:
        websocket_manager.disconnect(websocket)


@router.websocket("/tenant/{tenant_id}")
async def websocket_tenant_endpoint(
    websocket: WebSocket,
    tenant_id: str,
    token: Optional[str] = Query(None)
):
    """
    WebSocket endpoint for all workflow executions in a tenant

    Connect to this endpoint to receive real-time updates for all workflow
    executions within a specific tenant.

    **Query Parameters:**
    - token: JWT authentication token (recommended for tenant-wide access)

    **Events Sent:**
    - All events from all workflow executions in the tenant

    **Example Usage:**
    ```javascript
    const ws = new WebSocket(`ws://localhost:8001/ws/tenant/${tenantId}?token=${authToken}`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log('Tenant Event:', data.event, 'Execution:', data.execution_id);
    };
    ```
    """
    user_id = None

    # Token validation for tenant-level access (should be enforced)
    if token:
        try:
            # Add proper token validation
            # user_data = verify_token(token)
            # Verify user has access to this tenant
            # if user_data.get("tenant_id") != tenant_id:
            #     await websocket.close(code=1008, reason="Unauthorized")
            #     return
            # user_id = user_data.get("user_id")
            pass
        except Exception as e:
            logger.warning(f"WebSocket token validation failed: {e}")
            await websocket.close(code=1008, reason="Authentication failed")
            return

    await websocket_manager.connect(
        websocket,
        tenant_id=tenant_id,
        user_id=user_id
    )

    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "event": "connected",
            "tenant_id": tenant_id,
            "message": "Successfully connected to tenant workflow updates"
        })

        # Keep connection alive
        while True:
            try:
                data = await websocket.receive_text()

                if data == "ping":
                    await websocket.send_json({"event": "pong"})
                else:
                    try:
                        message = json.loads(data)
                        if message.get("action") == "stats":
                            stats = websocket_manager.get_connection_info()
                            await websocket.send_json({
                                "event": "stats_response",
                                "stats": stats
                            })
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON received: {data}")

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error in WebSocket receive loop: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for tenant: {tenant_id}")
    except Exception as e:
        logger.error(f"WebSocket error for tenant {tenant_id}: {e}")
    finally:
        websocket_manager.disconnect(websocket)


@router.get("/connections/info")
async def get_websocket_info():
    """
    Get information about active WebSocket connections

    Returns statistics about currently active WebSocket connections.

    **Response:**
    ```json
    {
        "total_connections": 5,
        "executions_tracked": 3,
        "tenants_connected": 2,
        "connections_by_execution": {
            "exec-id-1": 2,
            "exec-id-2": 1
        }
    }
    ```
    """
    return websocket_manager.get_connection_info()
