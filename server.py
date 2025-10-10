from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
import os
import logging
import uvicorn
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import PlainTextResponse

# Import simplified security features
try:
    from security.middleware import (
        SecurityHeadersMiddleware,
        RateLimitMiddleware,
        AuditLogMiddleware,
        InputValidationMiddleware
    )
    MIDDLEWARE_AVAILABLE = True
except ImportError:
    logging.warning("Full middleware not available, using basic security")
    from security.simple_middleware import add_security_headers, log_request
    MIDDLEWARE_AVAILABLE = False

# Load environment variables
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Import database connection
from database.connection import connect_database, disconnect_database, test_connection
from services.queue_service import queue_service
from services.scheduler_service import scheduler_service
from services.mllp_service import mllp_service
from services.retention_service import retention_service

# Import API routers
from api.hl7_ingestion_router import router as hl7_ingestion_router
from api.auth_router import router as auth_router
from api.hl7_router_new import router as hl7_router
from api.workflow_router_new import router as workflow_router
from api.analytics_router_new import router as analytics_router
from api.data_table_router_new import router as data_table_router
from api.vendor_endpoint_router_new import router as vendor_endpoint_router
from api.tenant_admin_router import router as tenant_admin_router
from api.tenant_user_router import router as tenant_user_router
from api.admin_data_router import router as admin_data_router
from api.settings_router import router as settings_router
from api.billing_router import router as billing_router
from api.admin_billing_router import router as admin_billing_router
from api.public_router import router as public_router
from api.sso_router import router as sso_router
from api.admin_plans_router import router as plans_router
from api.ai_workflow_router import router as ai_workflow_router
from api.admin.audit_log import router as audit_log_router
from api.quarantine_router import router as quarantine_router
from api.dlq_router import router as dlq_router
from api.fhir_subscription_router import router as fhir_router
from api.interop_messages_router import router as interop_messages_router
from api.dicom_analysis import router as dicom_analysis_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Silence health-check access logs (/api/health) while keeping other access logs
class _HealthAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            return "/api/health" not in msg
        except Exception:
            return True

logging.getLogger("uvicorn.access").addFilter(_HealthAccessFilter())

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Create FastAPI app
app = FastAPI(
    title="meddataflow",
    description="Modern HL7 message integration platform with intuitive English translation and visual workflow designer",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    redirect_slashes=False
)

# Add rate limiting to the app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Optional OpenTelemetry tracing
try:
    if os.getenv('OTEL_ENABLED', 'false').lower() == 'true':
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        provider = TracerProvider()
        endpoint = os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')
        if endpoint:
            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
            provider.add_span_processor(processor)
            trace.set_tracer_provider(provider)
            FastAPIInstrumentor.instrument_app(app)
            logging.info('OpenTelemetry tracing initialized')
except Exception as _otel_e:
    logging.warning(f"OpenTelemetry init failed: {_otel_e}")


# Security middleware (order matters - from innermost to outermost)
if MIDDLEWARE_AVAILABLE:
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AuditLogMiddleware)
    app.add_middleware(InputValidationMiddleware, max_request_size=10 * 1024 * 1024)  # 10MB for HL7 messages
    app.add_middleware(RateLimitMiddleware, requests_per_minute=100, requests_per_hour=5000)
    logging.info("✅ Full security middleware enabled")
else:
    logging.warning("⚠️ Using basic security - install full middleware for production")

    # Basic security headers middleware
    @app.middleware("http")
    async def basic_security_middleware(request, call_next):
        response = await call_next(request)
        if not MIDDLEWARE_AVAILABLE:
            response = add_security_headers(response)
            log_request(request)
        return response

# CORS middleware with debug logging
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3001").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept", "Origin", "Access-Control-Request-Method", "Access-Control-Request-Headers", "X-CSRF-Token"],
    max_age=86400  # 24 hours
)

# Create API router with /api prefix
api_router = APIRouter(prefix="/api")

# Health check endpoints
@api_router.get("/health")
async def health_check():
    """Health check endpoint"""
    db_healthy = await test_connection()
    # MLLP status
    try:
        mllp_status = mllp_service.get_status()
    except Exception:
        mllp_status = {'running': False}
    try:
        mllp_metrics = mllp_service.get_metrics()
    except Exception:
        mllp_metrics = {}

    return {
        "status": "healthy" if db_healthy else "unhealthy",
        "version": "1.0.0",
        "services": {
            "database": "connected" if db_healthy else "disconnected",
            "mllp": {
                "running": bool(mllp_status.get('running')),
                "host": mllp_status.get('host'),
                "port": mllp_status.get('port'),
                "tls_enabled": mllp_status.get('tls_enabled'),
                "mtls": mllp_status.get('mtls'),
                "metrics": {
                    "connections_active": mllp_metrics.get('connections_active'),
                    "connections_total": mllp_metrics.get('connections_total'),
                    "messages_total": mllp_metrics.get('messages_total')
                }
            }
        }
    }

@api_router.get("/mllp/status")
async def mllp_status():
    try:
        return {
            'status': mllp_service.get_status(),
            'metrics': mllp_service.get_metrics()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get MLLP status: {e}")

@api_router.get("/ready")
async def ready():
    db_healthy = await test_connection()
    if not db_healthy:
        raise HTTPException(status_code=503, detail="Database not ready")
    try:
        mllp = mllp_service.get_status()
    except Exception:
        mllp = {'running': False}
    return {
        'ready': True,
        'database': True,
        'mllp_running': bool(mllp.get('running'))
    }

@api_router.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "meddataflow Platform API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "auth": "/api/auth",
            "hl7": "/api/hl7", 
            "workflows": "/api/workflows",
            "analytics": "/api/analytics",
            "data-tables": "/api/data-tables"
        }
    }

# Include routers
app.include_router(hl7_ingestion_router)
app.include_router(auth_router)
app.include_router(hl7_router)
app.include_router(workflow_router)
app.include_router(analytics_router)
app.include_router(data_table_router)
app.include_router(vendor_endpoint_router)
app.include_router(tenant_admin_router)
app.include_router(tenant_user_router)
app.include_router(admin_data_router)
app.include_router(settings_router)
app.include_router(billing_router)
app.include_router(admin_billing_router)
app.include_router(public_router)
app.include_router(sso_router)
app.include_router(plans_router)
app.include_router(ai_workflow_router)
app.include_router(audit_log_router)
app.include_router(quarantine_router)
app.include_router(dlq_router)
app.include_router(fhir_router)
app.include_router(interop_messages_router)
app.include_router(dicom_analysis_router)
app.include_router(api_router)

# Serve static files (e.g., uploaded logos)
static_dir = ROOT_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize application on startup"""
    
    try:
        # Connect to database
        await connect_database()
        
        # Test database connection
        if await test_connection():
            pass
        else:
            logger.warning("Database health check failed")
        
        # Start queue service
        await queue_service.start()

        # Start scheduler service (opt-in via env)
        await scheduler_service.start()

        # Start MLLP listener (config via system settings/env)
        await mllp_service.start()

        # Start retention maintenance loop
        await retention_service.start()

        # Ensure settings tables exist
        from services.settings_service import settings_service
        await settings_service.ensure_settings_tables()

        # Initialize AI settings - admin must configure API key
        ai_settings = await settings_service.get_ai_settings()
        if not ai_settings.get("openrouter_api_key"):
            # Only set API key from environment variable for security
            api_key = os.getenv("OPENROUTER_API_KEY")
            if api_key:
                await settings_service.update_ai_settings({
                    "enabled": True,
                    "openrouter_api_key": api_key
                })
            else:
                logger.warning("AI API key not configured - admin must set via settings")

        
    except Exception as e:
        logger.error(f"Failed to initialize application: {e}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    
    # Stop queue service
    await queue_service.stop()

    # Stop scheduler service
    await scheduler_service.stop()

    # Stop MLLP listener
    await mllp_service.stop()

    # Stop retention loop
    await retention_service.stop()
    
    # Close database connections
    await disconnect_database()
    

# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code
        }
    )

@app.get("/metrics")
async def metrics():
    """Prometheus-compatible metrics exposition (basic)."""
    # Base gauges
    try:
        db_ok = await test_connection()
    except Exception:
        db_ok = False
    try:
        mllp_status = mllp_service.get_status()
        mllp_metrics = mllp_service.get_metrics()
    except Exception:
        mllp_status = {'running': False}
        mllp_metrics = {}

    lines = []
    def add_gauge(name: str, help_text: str, value):
        try:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            v = 1 if value is True else 0 if value is False else (value if value is not None else 0)
            lines.append(f"{name} {v}")
        except Exception:
            pass

    add_gauge('app_up', 'Application up status', True)
    add_gauge('db_up', 'Database connectivity status', bool(db_ok))
    add_gauge('mllp_running', 'MLLP listener running status', bool(mllp_status.get('running')))
    add_gauge('mllp_tls_enabled', 'MLLP TLS enabled', bool(mllp_status.get('tls_enabled')))
    add_gauge('mllp_connections_active', 'MLLP active TCP connections', mllp_metrics.get('connections_active'))
    add_gauge('mllp_connections_total', 'MLLP total TCP connections since start', mllp_metrics.get('connections_total'))
    add_gauge('mllp_messages_total', 'MLLP messages received since start', mllp_metrics.get('messages_total'))

    body = "\n".join(lines) + "\n"
    return PlainTextResponse(content=body, media_type='text/plain; version=0.0.4; charset=utf-8')

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions"""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "status_code": 500
        }
    )

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    host = os.getenv("HOST", "0.0.0.0")
    
    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=os.getenv("DEBUG", "false").lower() == "true",
        log_level="info"
    )
