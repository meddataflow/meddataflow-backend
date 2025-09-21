"""
Analytics Router - AsyncPG Compatible
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
import uuid

from models.hl7_message import HL7MessageRepository
from database.connection import fetch_one, fetch_all
from api.auth_deps import get_current_user, get_current_tenant

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# Pydantic models
class DashboardData(BaseModel):
    total_messages: int
    messages_today: int
    messages_this_week: int
    messages_this_month: int
    total_workflows: int
    active_workflows: int
    message_types_breakdown: List[Dict[str, Any]]
    daily_message_counts: List[Dict[str, Any]]
    workflow_execution_stats: Dict[str, Any]
    recent_activity: List[Dict[str, Any]] = []
    system_health: Dict[str, Any] = {}

class MessageTypeStats(BaseModel):
    message_type: str
    count: int
    percentage: float

class PeriodStats(BaseModel):
    total_messages: int
    processed_messages: int
    failed_messages: int
    success_rate: float
    avg_processing_time: Optional[float] = None

@router.get("/dashboard", response_model=DashboardData)
async def get_dashboard_data(
    days: int = Query(7, ge=1, le=365),
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get dashboard analytics data"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
        
        # Get message statistics
        hl7_stats = await HL7MessageRepository.get_message_stats(tenant_id)
        
        # Workflow stats (counts and execution aggregates)
        wf_counts = await fetch_one(
            """
            SELECT 
              COUNT(*) AS total_workflows,
              COUNT(CASE WHEN status = 'ACTIVE' THEN 1 END) AS active_workflows
            FROM workflows
            WHERE tenant_id = $1
            """,
            tenant_id,
        ) or {"total_workflows": 0, "active_workflows": 0}

        exec_stats = await fetch_one(
            """
            SELECT 
              COUNT(*) AS total_executions,
              COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) AS successful_executions,
              COUNT(CASE WHEN status = 'FAILED' THEN 1 END) AS failed_executions,
              AVG(execution_time_ms) AS avg_execution_time_ms
            FROM workflow_executions
            WHERE tenant_id = $1
            """,
            tenant_id,
        ) or {"total_executions": 0, "successful_executions": 0, "failed_executions": 0, "avg_execution_time_ms": 0}
        
        # Calculate time-based stats
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)
        
        # Real message type breakdown
        mt_rows = await fetch_all(
            """
            SELECT message_type, COUNT(*) AS c
            FROM hl7_messages
            WHERE tenant_id = $1
            GROUP BY message_type
            ORDER BY c DESC
            LIMIT 10
            """,
            tenant_id,
        )
        total_msgs = int(hl7_stats.get('total_messages', 0) or 0)
        message_types_breakdown = []
        for r in mt_rows:
            count = int(r['c'])
            pct = (count / total_msgs * 100.0) if total_msgs > 0 else 0.0
            message_types_breakdown.append({
                "type": r['message_type'] or 'UNKNOWN',
                "count": count,
                "percentage": round(pct, 2)
            })
        
        # Daily message counts for last N days, fill missing with zero
        from collections import defaultdict
        rows = await fetch_all(
            """
            SELECT DATE_TRUNC('day', created_at) AS d, COUNT(*) AS c
            FROM hl7_messages
            WHERE tenant_id = $1 AND created_at >= (DATE_TRUNC('day', NOW()) - INTERVAL '$2 days')
            GROUP BY 1
            ORDER BY 1
            """,
            tenant_id,
            days - 1,
        )
        counts = {r['d'].date().isoformat(): int(r['c']) for r in rows}
        daily_counts = []
        for i in range(days):
            dt = (now - timedelta(days=(days - 1 - i))).date().isoformat()
            daily_counts.append({"date": dt, "count": counts.get(dt, 0)})
        
        # Recent activity: last 10 events from messages and workflow executions
        recent_msgs = await fetch_all(
            """
            SELECT 'message' AS kind, created_at, status, message_type AS title, message_control_id AS ref
            FROM hl7_messages
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            LIMIT 10
            """,
            tenant_id,
        )
        recent_execs = await fetch_all(
            """
            SELECT 'workflow' AS kind, COALESCE(completed_at, started_at) AS created_at, status, w.name AS title, we.execution_id AS ref
            FROM workflow_executions we
            JOIN workflows w ON w.id = we.workflow_id
            WHERE we.tenant_id = $1
            ORDER BY COALESCE(completed_at, started_at) DESC
            LIMIT 10
            """,
            tenant_id,
        )
        combined = recent_msgs + recent_execs
        # Normalize and sort by created_at desc
        def _row_to_activity(r):
            return {
                "kind": r.get('kind'),
                "timestamp": (r.get('created_at') or now).isoformat(),
                "status": (r.get('status') or '').lower(),
                "title": r.get('title') or '',
                "ref": r.get('ref')
            }
        recent_activity = sorted([_row_to_activity(r) for r in combined], key=lambda x: x['timestamp'], reverse=True)[:10]

        # System health snapshot
        last_hour = now - timedelta(hours=1)
        last_hour_row = await fetch_one(
            """
            SELECT COUNT(*) AS total,
                   COUNT(CASE WHEN status = 'FAILED' THEN 1 END) AS failed
            FROM hl7_messages
            WHERE tenant_id = $1 AND created_at >= $2
            """,
            tenant_id, last_hour
        ) or {"total": 0, "failed": 0}
        total_lh = int(last_hour_row.get('total') or 0)
        failed_lh = int(last_hour_row.get('failed') or 0)
        system_health = {
            "database": "connected",
            "messages_last_hour": total_lh,
            "failed_last_hour": failed_lh,
            "error_rate_last_hour": round((failed_lh / total_lh * 100.0), 2) if total_lh > 0 else 0.0,
            "active_workflows": int(wf_counts.get('active_workflows') or 0),
            "avg_execution_time_ms": float(exec_stats.get('avg_execution_time_ms') or 0) if exec_stats.get('avg_execution_time_ms') is not None else None,
        }

        return DashboardData(
            total_messages=hl7_stats.get('total_messages', 0),
            messages_today=hl7_stats.get('received_today', 0),
            messages_this_week=sum([d['count'] for d in daily_counts[-7:]]) if len(daily_counts) >= 7 else sum([d['count'] for d in daily_counts]),
            messages_this_month=sum([d['count'] for d in daily_counts]),
            total_workflows=int(wf_counts.get('total_workflows') or 0),
            active_workflows=int(wf_counts.get('active_workflows') or 0),
            message_types_breakdown=message_types_breakdown,
            daily_message_counts=daily_counts,
            workflow_execution_stats={
                "total_executions": int(exec_stats.get('total_executions') or 0),
                "successful_executions": int(exec_stats.get('successful_executions') or 0),
                "failed_executions": int(exec_stats.get('failed_executions') or 0),
                "success_rate": (
                    (int(exec_stats.get('successful_executions') or 0) / int(exec_stats.get('total_executions') or 1)) * 100.0
                ) if int(exec_stats.get('total_executions') or 0) > 0 else 0.0
            },
            recent_activity=recent_activity,
            system_health=system_health
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch dashboard data: {str(e)}"
        )

@router.get("/stats", response_model=PeriodStats)
async def get_stats(
    period: str = Query("7d", regex="^(1d|7d|30d|90d)$"),
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get statistics for a specific time period"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
        
        # Parse period
        period_days = {
            "1d": 1,
            "7d": 7,
            "30d": 30,
            "90d": 90
        }[period]
        
        # Filter by period
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=period_days)
        # Totals over the period
        row = await fetch_one(
            """
            SELECT 
              COUNT(*) AS total,
              COUNT(CASE WHEN status = 'PROCESSED' THEN 1 END) AS processed,
              COUNT(CASE WHEN status = 'FAILED' THEN 1 END) AS failed,
              AVG(EXTRACT(EPOCH FROM (processed_at - created_at))) AS avg_secs
            FROM hl7_messages
            WHERE tenant_id = $1 AND created_at >= $2
            """,
            tenant_id, start
        ) or {"total": 0, "processed": 0, "failed": 0, "avg_secs": None}
        total = int(row['total'] or 0)
        processed = int(row['processed'] or 0)
        failed = int(row['failed'] or 0)
        
        success_rate = 0.0
        if total > 0:
            success_rate = (processed / total) * 100
            
        return PeriodStats(
            total_messages=total,
            processed_messages=processed,
            failed_messages=failed,
            success_rate=round(success_rate, 2),
            avg_processing_time=float(row['avg_secs']) if row.get('avg_secs') is not None else None
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch period stats: {str(e)}"
        )

@router.get("/message-types", response_model=List[MessageTypeStats])
async def get_message_types(
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Get message type statistics"""
    try:
        tenant_id = current_tenant['id']
        if isinstance(tenant_id, str):
            tenant_id = uuid.UUID(tenant_id)
        
        total_row = await fetch_one("SELECT COUNT(*) AS c FROM hl7_messages WHERE tenant_id = $1", tenant_id)
        total = int(total_row['c'] or 0) if total_row else 0
        rows = await fetch_all(
            """
            SELECT message_type, COUNT(*) AS c
            FROM hl7_messages
            WHERE tenant_id = $1
            GROUP BY message_type
            ORDER BY c DESC
            LIMIT 20
            """,
            tenant_id,
        )
        results: List[MessageTypeStats] = []
        for r in rows:
            c = int(r['c'])
            pct = (c / total * 100.0) if total > 0 else 0.0
            results.append(MessageTypeStats(message_type=r['message_type'] or 'UNKNOWN', count=c, percentage=round(pct, 2)))
        return results
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch message type stats: {str(e)}"
        )
