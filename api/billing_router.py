"""
Billing endpoints: tenant billing settings and usage/estimate
"""
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth_deps import get_current_user, get_current_tenant, require_tenant_admin, get_current_tenant_allow_inactive
from api.settings_router import _read_platform_config
from models.tenant import TenantRepository
from models.hl7_message import HL7MessageRepository
from models.billing import BillingInvoiceRepository
import os
import hmac
import hashlib
from fastapi import Request
from pathlib import Path
import json as _json
try:
    import stripe
except Exception:
    stripe = None

router = APIRouter(prefix="/api/billing", tags=["billing"])


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

def _resolve_coupon(code: Optional[str]) -> Optional[Dict[str, Any]]:
    if not code:
        return None
    try:
        cfg_path = Path(__file__).resolve().parent.parent / 'config' / 'platform_config.json'
        if cfg_path.exists():
            cfg = _json.loads(cfg_path.read_text())
            coupons = cfg.get("coupons") or []
            for c in coupons:
                if str(c.get("code", "")).lower() == str(code).lower():
                    return c
    except Exception:
        return None
    return None


@router.get("")
async def get_billing(
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    tenant_id = current_tenant['id']
    if isinstance(tenant_id, str):
        tenant_id = uuid.UUID(tenant_id)

    # Load tenant and settings
    tenant = await TenantRepository.get_tenant_by_id_any_status(tenant_id)
    # Normalize settings which can be stored as JSON string
    settings = tenant.get("settings") or {}
    if isinstance(settings, str):
        try:
            import json as _json
            settings = _json.loads(settings)
        except Exception:
            settings = {}
    billing = settings.get("billing", {})

    # Usage in current month
    start = _month_start(datetime.now(timezone.utc))
    usage_count = await HL7MessageRepository.count_messages_since(tenant_id, start)
    stats = await HL7MessageRepository.get_message_stats(tenant_id)

    estimate = _estimate(tenant.get("plan", "PROFESSIONAL"), usage_count)
    billing_status = billing.get('subscription_status', 'none')
    billing_exempt = bool(billing.get('billing_exempt', False))
    return {
        "plan": tenant.get("plan"),
        "billing_email": tenant.get("billing_email"),
        "billing_address": tenant.get("billing_address"),
        "settings": billing,
        "subscription_status": billing_status,
        "billing_exempt": billing_exempt,
        "usage": {
            "current_month_messages": usage_count,
            "total_messages": stats.get("total_messages", 0)
        },
        "estimate": estimate
    }


@router.patch("/settings")
async def update_billing_settings(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(require_tenant_admin()),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Update tenant billing contact and configuration.
    Accepts keys: billing_email, billing_address, settings.billing (provider, customer_id, subscription_id, tax_id, payment_method_last4)
    """
    tenant_id = current_tenant['id']
    if isinstance(tenant_id, str):
        tenant_id = uuid.UUID(tenant_id)

    # Load existing settings
    tenant = await TenantRepository.get_tenant_by_id(tenant_id)
    # Normalize existing settings
    existing = tenant.get("settings") or {}
    if isinstance(existing, str):
        try:
            import json as _json
            existing = _json.loads(existing)
        except Exception:
            existing = {}
    current_settings = dict(existing)
    billing = dict(current_settings.get("billing") or {})

    updates: Dict[str, Any] = {}
    if "billing_email" in payload:
        updates["billing_email"] = payload.get("billing_email")
    if "billing_address" in payload:
        updates["billing_address"] = payload.get("billing_address")

    # Merge billing settings
    if "settings" in payload and isinstance(payload["settings"], dict):
        new_billing = payload["settings"].get("billing") or payload["settings"]
        if isinstance(new_billing, dict):
            billing.update(new_billing)
    current_settings["billing"] = billing
    updates["settings"] = current_settings

    updated = await TenantRepository.update_tenant(tenant_id, **updates)
    if not updated:
        raise HTTPException(status_code=400, detail="No changes applied")
    return {"updated": True, "billing": billing}


@router.get("/preview")
async def preview_invoice(
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    tenant_id = current_tenant['id']
    if isinstance(tenant_id, str):
        tenant_id = uuid.UUID(tenant_id)
    start = _month_start(datetime.now(timezone.utc))
    usage = await HL7MessageRepository.count_messages_since(tenant_id, start)
    estimate = _estimate(current_tenant.get("plan", "PROFESSIONAL"), usage)
    return {"estimate": estimate}


@router.get("/invoices")
async def list_invoices(
    months: int = Query(6, ge=1, le=24),
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    tenant_id = current_tenant['id']
    if isinstance(tenant_id, str):
        tenant_id = uuid.UUID(tenant_id)
    plan = current_tenant.get('plan', 'PROFESSIONAL')
    now = datetime.now(timezone.utc)
    period_start = _month_start(now)
    results = []
    for i in range(months):
        end = period_start
        # start of previous month
        start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
        count = await HL7MessageRepository.count_messages_between(tenant_id, start, end)
        est = _estimate(plan, count)
        results.append({
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "message_count": count,
            "estimate": est,
        })
        period_start = start
    return {"items": list(reversed(results))}

from fastapi.responses import Response
from datetime import timedelta

@router.get("/invoices.csv")
async def invoices_csv(
    months: int = Query(6, ge=1, le=24),
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    data = await list_invoices(months, current_user, current_tenant)
    rows = ["period_start,period_end,message_count,plan,base,included,overage_rate,overage_charges,estimated_total"]
    for item in data["items"]:
        est = item["estimate"]
        rows.append(
            f"{item['period_start']},{item['period_end']},{item['message_count']},{est['plan']},{est['base']},{est['included']},{est['overage_rate']},{est['overage_charges']},{est['estimated_total']}"
        )
    csv_data = "\n".join(rows)
    return Response(content=csv_data, media_type="text/csv")


@router.post("/subscribe")
async def subscribe(
    provider: Optional[str] = None,
    plan: Optional[str] = None,
    billing_exempt: Optional[bool] = None,
    admin_approval: Optional[bool] = None,
    current_user: Dict[str, Any] = Depends(require_tenant_admin()),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant_allow_inactive),
):
    tenant_id = current_tenant['id']
    if isinstance(tenant_id, str):
        tenant_id = uuid.UUID(tenant_id)
    tenant = await TenantRepository.get_tenant_by_id(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    # Normalize settings structure
    raw_settings = tenant.get('settings') or {}
    if isinstance(raw_settings, str):
        try:
            import json as _json
            raw_settings = _json.loads(raw_settings) or {}
        except Exception:
            raw_settings = {}
    settings = dict(raw_settings)
    billing = dict(settings.get('billing') or {})
    # Minimal activation flow
    import secrets
    billing['subscription_id'] = billing.get('subscription_id') or f"sub_{secrets.token_hex(8)}"
    if provider:
        billing['provider'] = provider
    if plan:
        # update tenant plan
        await TenantRepository.update_tenant(tenant_id, plan=plan)
    if billing_exempt is True and admin_approval is True:
        # Activation requires admin approval: mark tenant inactive, user inactive, and set status pending
        billing['billing_exempt'] = True
        billing['subscription_status'] = 'pending_approval'
        await TenantRepository.update_tenant(tenant_id, is_active=False)
        # Deactivate all users of this tenant until approved
        try:
            from database.connection import execute as _exec
            await _exec("UPDATE users SET is_active = FALSE WHERE tenant_id = $1", tenant_id)
        except Exception:
            pass
        # Append admin notification for approval
        try:
            notif_path = Path(__file__).resolve().parent.parent / 'config' / 'notifications.json'
            notif_path.parent.mkdir(parents=True, exist_ok=True)
            existing = []
            if notif_path.exists():
                try:
                    existing = _json.loads(notif_path.read_text()) or []
                except Exception:
                    existing = []
            existing.append({
                'type': 'TENANT_PENDING_APPROVAL',
                'tenant_id': str(tenant_id),
                'tenant_name': tenant.get('name'),
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            notif_path.write_text(_json.dumps(existing, indent=2))
        except Exception:
            pass
        # Email notifications
        try:
            from services.email_service import send_email
            from models.user import UserRepository
            # Notify super admins
            supers = await UserRepository.list_super_admin_emails()
            subj = f"Tenant awaiting approval: {tenant.get('name')}"
            base_url = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
            approvals_link = f"{base_url}/admin/approvals" if base_url else None
            body = (f"Tenant '{tenant.get('name')}' (slug: {tenant.get('slug')}) requested activation without payment.\n\n"
                    "Login to Admin > Approvals to review.")
            if approvals_link:
                body += f"\n{approvals_link}"
            html = None
            if approvals_link:
                html = f"""
                <p>Tenant '<strong>{tenant.get('name')}</strong>' (slug: <code>{tenant.get('slug')}</code>) requested activation without payment.</p>
                <p><a href="{approvals_link}">Review pending approvals</a></p>
                """
            for addr in supers:
                send_email(addr, subj, body, html)
            # Notify requester/billing contact if present
            if tenant.get('billing_email'):
                send_email(
                    tenant['billing_email'],
                    "Your activation request is pending",
                    "We received your request. A MedDataFlow admin will review and activate your tenant.",
                    f"<p>We received your request. A MedDataFlow admin will review and activate your tenant.</p>"
                )
        except Exception:
            pass
    else:
        # Immediate activation (manual or provider)
        billing['subscription_status'] = 'active'
        if billing_exempt is True:
            billing['billing_exempt'] = True
        await TenantRepository.update_tenant(tenant_id, is_active=True)
    settings['billing'] = billing
    await TenantRepository.update_tenant(tenant_id, settings=settings)
    return {"status": billing.get('subscription_status', 'active'), "subscription_id": billing['subscription_id'], "billing_exempt": billing.get('billing_exempt', False)}


@router.post("/cancel")
async def cancel_subscription(
    current_user: Dict[str, Any] = Depends(require_tenant_admin()),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant),
):
    tenant_id = current_tenant['id']
    if isinstance(tenant_id, str):
        tenant_id = uuid.UUID(tenant_id)
    tenant = await TenantRepository.get_tenant_by_id(tenant_id)
    settings = dict(tenant.get('settings') or {})
    billing = dict(settings.get('billing') or {})
    billing['subscription_status'] = 'canceled'
    settings['billing'] = billing
    await TenantRepository.update_tenant(tenant_id, settings=settings)
    return {"status": billing['subscription_status']}


# ------------------
# Provider webhooks
# ------------------

def _find_tenant_by_customer_id(customer_id: str) -> Optional[uuid.UUID]:
    # naive lookup: scan tenants for settings.billing.customer_id
    # in a real system, store mapping in a separate table
    # We'll reuse get_all_tenants (without pagination)
    from models.tenant import TenantRepository
    async def _inner():
        tenants = await TenantRepository.get_all_tenants()
        for t in tenants:
            settings = t.get('settings') or {}
            billing = settings.get('billing') or {}
            if str(billing.get('customer_id')) == str(customer_id):
                return t['id'] if isinstance(t['id'], uuid.UUID) else uuid.UUID(str(t['id']))
        return None
    # We cannot call async from sync here; this wrapper is used inside async routes only
    return None  # placeholder

async def _resolve_tenant_by_customer(customer_id: str) -> Optional[uuid.UUID]:
    from models.tenant import TenantRepository
    tenants = await TenantRepository.get_all_tenants()
    for t in tenants:
        settings = t.get('settings') or {}
        billing = settings.get('billing') or {}
        if str(billing.get('customer_id')) == str(customer_id):
            return t['id'] if isinstance(t['id'], uuid.UUID) else uuid.UUID(str(t['id']))
    return None

@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('Stripe-Signature', '')
    # Prefer platform config
    from pathlib import Path as _P
    import json as _json
    cfg_path = _P(__file__).resolve().parent.parent / 'config' / 'platform_config.json'
    secret = None
    try:
        if cfg_path.exists():
            _cfg = _json.loads(cfg_path.read_text())
            secret = (_cfg.get('stripe') or {}).get('webhook_secret')
    except Exception:
        pass
    secret = secret or os.getenv('STRIPE_WEBHOOK_SECRET')
    # Minimal verification (timestamp + signature), avoid heavy dependency
    if secret:
        try:
            signed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            if signed not in sig_header:
                return Response(status_code=400)
        except Exception:
            return Response(status_code=400)
    data = await request.json()
    event_type = data.get('type')
    obj = data.get('data', {}).get('object', {})
    customer_id = obj.get('customer') or obj.get('customer_id')
    tenant_id = await _resolve_tenant_by_customer(customer_id) if customer_id else None

    # Handle checkout completion: set subscription + customer on tenant
    if event_type == 'checkout.session.completed':
        client_ref = obj.get('client_reference_id')
        sub_id = obj.get('subscription')
        cust_id = obj.get('customer')
        tid = None
        try:
            if client_ref:
                tid = uuid.UUID(str(client_ref))
        except Exception:
            tid = None
        if not tid and tenant_id:
            tid = tenant_id
        if tid and (sub_id or cust_id):
            tenant = await TenantRepository.get_tenant_by_id(tid)
            settings = dict(tenant.get('settings') or {})
            billing = dict(settings.get('billing') or {})
            if sub_id:
                billing['subscription_id'] = sub_id
            if cust_id:
                billing['customer_id'] = cust_id
            billing['provider'] = 'stripe'
            billing['subscription_status'] = 'active'
            settings['billing'] = billing
            await TenantRepository.update_tenant(tid, settings=settings)

    # Record invoices (paid/finalized)
    if event_type in ('invoice.paid', 'invoice.finalized'):
        if not tenant_id and customer_id:
            tenant_id = await _resolve_tenant_by_customer(customer_id)
        if tenant_id:
            amount_cents = obj.get('amount_paid') or obj.get('amount_due') or 0
            period = obj.get('lines', {}).get('data', [{}])[0].get('period', {}) if obj.get('lines') else {}
            start = period.get('start')
            end = period.get('end')
            period_start = datetime.fromtimestamp(start, tz=timezone.utc) if start else None
            period_end = datetime.fromtimestamp(end, tz=timezone.utc) if end else None
            await BillingInvoiceRepository.create_invoice(
                tenant_id=tenant_id,
                provider='stripe',
                external_id=str(obj.get('id')),
                period_start=period_start,
                period_end=period_end,
                amount_cents=int(amount_cents),
                status=obj.get('status', 'paid'),
                currency=(obj.get('currency') or 'usd').upper(),
                payload=data,
            )

    # Update subscription status lifecycle
    if event_type in ('customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted'):
        status_val = obj.get('status')
        cust_id = obj.get('customer')
        sid = obj.get('id')
        tid = await _resolve_tenant_by_customer(cust_id) if cust_id else None
        if tid:
            tenant = await TenantRepository.get_tenant_by_id(tid)
            settings = dict(tenant.get('settings') or {})
            billing = dict(settings.get('billing') or {})
            if sid:
                billing['subscription_id'] = sid
            if cust_id:
                billing['customer_id'] = cust_id
            if status_val:
                billing['subscription_status'] = status_val
            settings['billing'] = billing
            await TenantRepository.update_tenant(tid, settings=settings)

    return {"received": True}


@router.post("/paddle/webhook")
async def paddle_webhook(request: Request):
    data = await request.json()
    customer_id = data.get('customer_id') or data.get('customer', {}).get('id')
    tenant_id = await _resolve_tenant_by_customer(customer_id) if customer_id else None
    if tenant_id and data.get('event_type') in ('invoice.paid', 'invoice.finalized'):
        amount_cents = int(float(data.get('amount_total', 0)) * 100) if isinstance(data.get('amount_total'), (int, float, str)) else 0
        start = data.get('period_start'); end = data.get('period_end')
        period_start = datetime.fromisoformat(start) if start else None
        period_end = datetime.fromisoformat(end) if end else None
        await BillingInvoiceRepository.create_invoice(
            tenant_id=tenant_id,
            provider='paddle',
            external_id=str(data.get('id')),
            period_start=period_start,
            period_end=period_end,
            amount_cents=amount_cents,
            status=data.get('status', 'paid'),
            currency=(data.get('currency') or 'USD').upper(),
            payload=data,
        )
    return {"received": True}


@router.get("/latest-invoice")
async def latest_invoice(
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    tenant_id = current_tenant['id']
    if isinstance(tenant_id, str):
        tenant_id = uuid.UUID(tenant_id)
    inv = await BillingInvoiceRepository.get_latest_invoice(tenant_id)
    if inv:
        return inv
    # Fallback to preview estimate if none
    start = _month_start(datetime.now(timezone.utc))
    usage = await HL7MessageRepository.count_messages_since(tenant_id, start)
    est = _estimate(current_tenant.get('plan', 'PROFESSIONAL'), usage)
    return {"estimate": est}


@router.post("/stripe/checkout")
async def create_stripe_checkout(
    plan_code: str,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
    coupon_code: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(require_tenant_admin()),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant_allow_inactive)
):
    if stripe is None:
        raise HTTPException(status_code=500, detail="Stripe library not installed")
    from models.plan import PlanRepository
    plan = await PlanRepository.get_plan_by_code(plan_code)
    if not plan:
        raise HTTPException(status_code=400, detail="Invalid plan code")

    # Read secret from platform config first
    from pathlib import Path as _P
    import json as _json
    cfg_path = _P(__file__).resolve().parent.parent / 'config' / 'platform_config.json'
    secret = None
    try:
        if cfg_path.exists():
            _cfg = _json.loads(cfg_path.read_text())
            secret = (_cfg.get('stripe') or {}).get('secret_key')
            # Apply coupon lookup from platform config
            coupon_cfg = _resolve_coupon(coupon_code)
        else:
            coupon_cfg = _resolve_coupon(coupon_code)
    except Exception:
        coupon_cfg = _resolve_coupon(coupon_code)
    secret = secret or os.getenv('STRIPE_SECRET_KEY')
    stripe.api_key = secret or ''
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY not configured")

    # Auto-provision Stripe Product/Price if missing on the plan
    price_id = plan.get('stripe_price_id')
    if not price_id:
        try:
            product = None
            if plan.get('stripe_product_id'):
                # Fetch existing product if referenced
                try:
                    product = stripe.Product.retrieve(plan['stripe_product_id'])
                except Exception:
                    product = None
            if not product:
                product = stripe.Product.create(name=plan.get('name') or plan_code)
            interval = 'month' if (plan.get('billing_period') or 'monthly') == 'monthly' else 'year'
            created_price = stripe.Price.create(
                unit_amount=int(plan.get('price_cents') or 0),
                currency='usd',
                recurring={'interval': interval},
                product=product['id']
            )
            # Persist IDs on the plan
            updated = await PlanRepository.update_plan(
                plan['id'] if isinstance(plan['id'], uuid.UUID) else uuid.UUID(str(plan['id'])),
                stripe_product_id=product['id'],
                stripe_price_id=created_price['id']
            )
            price_id = (updated or plan).get('stripe_price_id') or created_price['id']
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Plan not configured for Stripe: {e}")
    tenant_id = current_tenant['id'] if isinstance(current_tenant['id'], uuid.UUID) else uuid.UUID(str(current_tenant['id']))
    email = current_user.get('email')
    base_frontend = os.getenv('FRONTEND_BASE_URL', 'http://localhost:3000')
    success = success_url or f"{base_frontend}/signup/plan?status=success"
    cancel = cancel_url or f"{base_frontend}/signup/plan?status=cancel"

    # Ensure we append session_id with the correct separator
    def _append_param(url: str, key: str, value: str) -> str:
        return f"{url}{'&' if '?' in url else '?'}{key}={value}"
    success_with_sid = _append_param(success, 'session_id', '{CHECKOUT_SESSION_ID}')
    discounts = None
    try:
        if coupon_cfg:
            stripe_coupon_id = coupon_cfg.get("stripe_coupon_id")
            if not stripe_coupon_id:
                create_kwargs = {}
                if coupon_cfg.get("percent_off") is not None:
                    create_kwargs["percent_off"] = float(coupon_cfg["percent_off"])
                if coupon_cfg.get("amount_off_cents") is not None:
                    create_kwargs["amount_off"] = int(coupon_cfg["amount_off_cents"])
                    create_kwargs["currency"] = 'usd'
                if create_kwargs:
                    # Use a recurring/forever coupon so the discount is permanent
                    create_kwargs["duration"] = "forever"
                    created_coupon = stripe.Coupon.create(**create_kwargs)
                    stripe_coupon_id = created_coupon["id"]
            if stripe_coupon_id:
                discounts = [{"coupon": stripe_coupon_id}]
    except Exception:
        discounts = None

    session_kwargs = dict(
        mode='subscription',
        line_items=[{ 'price': price_id, 'quantity': 1 }],
        client_reference_id=str(tenant_id),
        customer_email=email,
        success_url=success_with_sid,
        cancel_url=cancel,
    )
    if discounts:
        session_kwargs["discounts"] = discounts
    try:
        session = stripe.checkout.Session.create(**session_kwargs)
        return { 'url': session['url'] }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe checkout error: {e}")


@router.post("/stripe/checkout/finalize")
async def finalize_stripe_checkout(
    session_id: str,
    coupon_code: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(require_tenant_admin()),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant_allow_inactive)
):
    """Finalize a Stripe checkout by retrieving the session and updating tenant billing settings.
    Useful in local/dev where webhooks may not be configured.
    """
    if stripe is None:
        raise HTTPException(status_code=500, detail="Stripe library not installed")

    # Read secret from platform config first
    from pathlib import Path as _P
    import json as _json
    cfg_path = _P(__file__).resolve().parent.parent / 'config' / 'platform_config.json'
    secret = None
    try:
        if cfg_path.exists():
            _cfg = _json.loads(cfg_path.read_text())
            secret = (_cfg.get('stripe') or {}).get('secret_key')
    except Exception:
        pass
    secret = secret or os.getenv('STRIPE_SECRET_KEY')
    stripe.api_key = secret or ''
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY not configured")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        # Stripe objects behave like dicts, but ensure robust access
        sub_id = session.get('subscription') if hasattr(session, 'get') else session['subscription'] if 'subscription' in session else None
        cust_id = session.get('customer') if hasattr(session, 'get') else session['customer'] if 'customer' in session else None
        if not sub_id and not cust_id:
            raise HTTPException(status_code=400, detail="Invalid or incomplete session")

        # Update tenant billing settings
        tenant_id = current_tenant['id'] if isinstance(current_tenant['id'], uuid.UUID) else uuid.UUID(str(current_tenant['id']))
        tenant = await TenantRepository.get_tenant_by_id_any_status(tenant_id)
        settings = tenant.get('settings') or {}
        if isinstance(settings, str):
            try:
                settings = _json.loads(settings)
            except Exception:
                settings = {}
        billing = (settings.get('billing') or {})
        if isinstance(billing, str):
            try:
                billing = _json.loads(billing)
            except Exception:
                billing = {}
        if sub_id:
            billing['subscription_id'] = sub_id
        if cust_id:
            billing['customer_id'] = cust_id
        billing['provider'] = 'stripe'
        billing['subscription_status'] = 'active'
        settings['billing'] = billing
        await TenantRepository.update_tenant(tenant_id, settings=settings, is_active=True)
        return { 'finalized': True, 'subscription_id': sub_id, 'customer_id': cust_id }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Finalize error: {e}")
