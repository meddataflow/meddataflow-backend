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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

# Add debug middleware to log CORS issues
@app.middleware("http")
async def cors_debug_middleware(request: Request, call_next):
    if request.method == "OPTIONS":

    response = await call_next(request)

    if request.method == "OPTIONS":

    return response

# Create API router with /api prefix
api_router = APIRouter(prefix="/api")

# Health check endpoints
@api_router.get("/health")
async def health_check():
    """Health check endpoint"""
    db_healthy = await test_connection()
    
    return {
        "status": "healthy" if db_healthy else "unhealthy",
        "version": "1.0.0",
        "services": {
            "database": "connected" if db_healthy else "disconnected"
        }
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
        else:
            logger.warning("Database health check failed")
        
        # Start queue service
        await queue_service.start()

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
