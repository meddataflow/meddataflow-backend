"""
Admin billing overview across all tenants
"""
from typing import Dict, Any, List
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, Query

from api.auth_deps import require_super_admin
from models.tenant import TenantRepository
from models.hl7_message import HL7MessageRepository
from models.billing import BillingInvoiceRepository
from fastapi.responses import Response
from datetime import timedelta
import os
import json as _json

try:
    import stripe
except Exception:
    stripe = None
from services.settings_service import settings_service

router = APIRouter(prefix="/api/admin/billing", tags=["admin-billing"])


def _month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _estimate(plan: str, monthly_messages: int) -> Dict[str, Any]:
    plan = (plan or "PROFESSIONAL").upper()
    pricing = {
        "FREE": {"base": 0, "included": 1000, "overage": 0.002},
        "PROFESSIONAL": {"base": 99, "included": 100000, "overage": 0.001},
        "ENTERPRISE": {"base": 999, "included": 2000000, "overage": 0.0005},
    }
    p = pricing.get(plan, pricing["PROFESSIONAL"])
    over = max(0, monthly_messages - p["included"]) * p["overage"]
    total = p["base"] + over
    return {
        "plan": plan,
        "base": p["base"],
        "included": p["included"],
        "overage_rate": p["overage"],
        "monthly_messages": monthly_messages,
        "overage_charges": round(over, 2),
        "estimated_total": round(total, 2),
    }


@router.get("")
async def list_billing_overview(
    _: Dict[str, Any] = Depends(require_super_admin()),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    tenants = await TenantRepository.get_all_tenants()
    tenants = tenants[offset:offset+limit]
    start = _month_start(datetime.now(timezone.utc))
    items: List[Dict[str, Any]] = []
    for t in tenants:
        tid = t["id"] if isinstance(t["id"], uuid.UUID) else uuid.UUID(str(t["id"]))
        usage = await HL7MessageRepository.count_messages_since(tid, start)
        est = _estimate(t.get("plan", "PROFESSIONAL"), usage)
        # Normalize settings possibly stored as string
        settings = t.get("settings") or {}
        if isinstance(settings, str):
            try:
                import json as _json
                settings = _json.loads(settings)
            except Exception:
                settings = {}
        billing = settings.get("billing", {})
        items.append({
            "tenant_id": str(t["id"]),
            "tenant_name": t["name"],
            "tenant_slug": t["slug"],
            "plan": t.get("plan"),
            "billing_email": t.get("billing_email"),
            "monthly_usage": usage,
            "estimate": est,
            "billing_settings": billing,
            "created_at": t.get("created_at"),
        })
    return {"items": items, "count": len(items)}


@router.get("/invoices")
async def admin_invoices(
    _: Dict[str, Any] = Depends(require_super_admin()),
    tenant_id: str = Query(...),
    months: int = Query(6, ge=1, le=24),
):
    tid = uuid.UUID(tenant_id)
    tenant = await TenantRepository.get_tenant_by_id(tid)
    if not tenant:
        return {"items": []}
    plan = tenant.get('plan', 'PROFESSIONAL')
    now = datetime.now(timezone.utc)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    items = []
    for i in range(months):
        end = period_start
        start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
        count = await HL7MessageRepository.count_messages_between(tid, start, end)
        est = _estimate(plan, count)
        items.append({
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "message_count": count,
            "estimate": est,
        })
        period_start = start
    return {"items": list(reversed(items))}


@router.get("/invoices.csv")
async def admin_invoices_csv(
    _: Dict[str, Any] = Depends(require_super_admin()),
    tenant_id: str = Query(...),
    months: int = Query(6, ge=1, le=24),
):
    data = await admin_invoices(_, tenant_id, months)  # type: ignore
    rows = ["period_start,period_end,message_count,plan,base,included,overage_rate,overage_charges,estimated_total"]
    for item in data["items"]:
        est = item["estimate"]
        rows.append(
            f"{item['period_start']},{item['period_end']},{item['message_count']},{est['plan']},{est['base']},{est['included']},{est['overage_rate']},{est['overage_charges']},{est['estimated_total']}"
        )
    return Response("\n".join(rows), media_type="text/csv")


@router.get("/invoice-latest")
async def admin_latest_invoice_url(
    _: Dict[str, Any] = Depends(require_super_admin()),
    tenant_id: str = Query(...),
):
    """Return the latest invoice PDF/hosted URL for a tenant, preferring stored data then Stripe."""
    tid = uuid.UUID(tenant_id)
    tenant = await TenantRepository.get_tenant_by_id_any_status(tid)
    if not tenant:
        return Response(status_code=404)

    # First, try stored invoices
    latest = await BillingInvoiceRepository.get_latest_invoice(tid)
    if latest:
        try:
            payload = latest.get("payload")
            if isinstance(payload, str):
                payload = _json.loads(payload)
            if isinstance(payload, dict):
                url = payload.get("invoice_pdf") or payload.get("hosted_invoice_url")
                if url:
                    return {"invoice_id": latest.get("external_id"), "url": url}
        except Exception:
            pass

    # Next, try Stripe direct lookup if configured
    cfg = await settings_service.get_platform_config()
    stripe_cfg = cfg.get("stripe") or {}
    secret = stripe_cfg.get("secret_key") or os.getenv("STRIPE_SECRET_KEY")
    if not secret or stripe is None:
        return Response(status_code=404)

    billing = {}
    settings = tenant.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = _json.loads(settings)
        except Exception:
            settings = {}
    billing = settings.get("billing") or {}
    customer_id = billing.get("customer_id")
    if not customer_id:
        return Response(status_code=404)

    try:
        stripe.api_key = secret
        invoices = stripe.Invoice.list(customer=customer_id, limit=1)
        data = getattr(invoices, "data", None) or []
        inv = data[0] if data else None
        if not inv:
            return Response(status_code=404)
        url = inv.get("invoice_pdf") or inv.get("hosted_invoice_url")
        if not url:
            return Response(status_code=404)
        return {"invoice_id": inv.get("id"), "url": url}
    except Exception:
        return Response(status_code=404)
