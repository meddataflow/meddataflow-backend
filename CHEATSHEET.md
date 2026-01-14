# Backend Developer Cheatsheet

## Quick Start
```bash
# Start server (hot reload)
uvicorn server:app --reload --port 8001

# Run migrations
alembic upgrade head

# Install dependencies
pip install -r requirements.txt
```

## Common Commands

### Server Management
| Action | Command |
|--------|---------|
| **Start Dev Server** | `uvicorn server:app --reload --port 8001` |
| **Start Prod Server** | `./startup.sh` or `uvicorn server:app --host 0.0.0.0 --port 8001` |
| **Check Health** | `curl http://localhost:8001/api/health` |
| **Check Readiness** | `curl http://localhost:8001/api/ready` |
| **View Metrics** | `curl http://localhost:8001/api/metrics` |

### Database & Migrations
| Action | Command |
|--------|---------|
| **Create Migration** | `alembic revision -m "message"` |
| **Apply Migrations** | `alembic upgrade head` |
| **Rollback** | `alembic downgrade -1` |
| **Reset DB (Dev)** | `python database/init_db.py reset` |
| **View Current** | `alembic current` |
| **View History** | `alembic history` |

### Docker
| Action | Command |
|--------|---------|
| **Build & Run** | `docker-compose up --build` |
| **Stop** | `docker-compose down` |
| **View Logs** | `docker-compose logs -f backend` |
| **Shell Access** | `docker exec -it backend_container /bin/bash` |
| **Check Stats** | `docker stats` |

---

## Project Structure

```
backend/
├── server.py              # Main FastAPI app, routers, startup/shutdown
├── api/                   # API routers (25 router files)
│   ├── auth_router.py         # Authentication endpoints
│   ├── auth_deps.py           # Auth dependencies (get_current_user, etc.)
│   ├── workflow_router_new.py # Workflow CRUD & execution
│   ├── hl7_router_new.py      # HL7 message management
│   ├── hl7_ingestion_router.py# HL7 ingestion endpoints
│   ├── billing_router.py      # Stripe billing integration
│   ├── analytics_router_new.py# Dashboard statistics
│   ├── admin_*.py             # Super admin endpoints
│   └── tenant_*.py            # Tenant admin endpoints
├── models/                # SQLAlchemy models (21 model files)
│   ├── workflow.py            # Workflow, WorkflowActivity, Execution
│   ├── user.py                # User, UserRole
│   ├── tenant.py              # Tenant, multi-tenancy
│   ├── hl7_message.py         # HL7Message storage
│   └── vendor_endpoint.py     # Vendor endpoints
├── services/              # Business logic (18 service files)
│   ├── workflow_execution_service.py  # Activity processor registry
│   ├── ai_service.py          # OpenRouter AI integration
│   ├── hl7_parser.py          # HL7 parsing (74KB!)
│   ├── auth_service.py        # JWT, passwords, 2FA
│   ├── mllp_service.py        # MLLP server
│   └── queue_service.py       # Redis queue
├── processors/            # Activity processors (18 processor files)
│   ├── hl7_processors.py      # HL7 parsing, transformation
│   ├── interoperability_processors.py # FHIR, DICOM, X12, CDA
│   ├── communication_processors.py    # HTTP, Email, TCP
│   └── control_processors.py  # Conditions, loops, validation
├── database/              # Database layer
│   ├── connection.py          # Async PostgreSQL pool
│   ├── schema.py              # Raw SQL table definitions
│   └── init_db.py             # DB initialization
├── security/              # Security middleware
│   ├── middleware.py          # Headers, rate limit, audit
│   └── secure_sandbox.py      # Code execution sandbox
└── config/                # Configuration files
    └── platform_config.json   # Platform-wide settings
```

---

## Environment Variables

### Required
| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection | `postgresql://user:pass@localhost:5433/db` |
| `SECRET_KEY` | App secret key | `your_super_secure_key` |
| `JWT_SECRET_KEY` | JWT signing key | `your_jwt_secret_key` |

### Application
| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Server port | `8001` |
| `HOST` | Server host | `0.0.0.0` |
| `DEBUG` | Debug mode | `false` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `CORS_ORIGINS` | Allowed origins | `http://localhost:3001` |

### Database & Redis
| Variable | Description | Example |
|----------|-------------|---------|
| `POSTGRES_DB` | Database name | `meddataflow` |
| `POSTGRES_USER` | Database user | `meddataflow` |
| `POSTGRES_PASSWORD` | Database password | - |
| `REDIS_URL` | Redis connection | `redis://localhost:6380` |

### JWT Configuration
| Variable | Description | Default |
|----------|-------------|---------|
| `JWT_ALGORITHM` | Algorithm | `HS256` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token expiry | `30` |

### Integrations (Optional)
| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | AI workflow generation |
| `AWS_ACCESS_KEY_ID` | S3 storage |
| `AWS_SECRET_ACCESS_KEY` | S3 storage |
| `S3_BUCKET_NAME` | S3 bucket |
| `SMTP_SERVER` | Email notifications |
| `SMTP_PORT` | SMTP port (default 587) |
| `FRONTEND_URL` | Frontend app URL |
| `BACKEND_URL` | Backend API URL |

---

## API Endpoints Reference

### Authentication (`/api/auth`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/login` | User login (returns JWT) |
| POST | `/register` | User registration |
| POST | `/refresh` | Refresh access token |
| POST | `/logout` | User logout |
| GET | `/me` | Get current user profile |
| GET | `/tenant` | Get current tenant info |
| POST | `/switch-tenant` | Switch active tenant |
| POST | `/2fa/setup` | Setup 2FA |
| POST | `/2fa/verify` | Verify 2FA code |
| POST | `/2fa/disable` | Disable 2FA |
| POST | `/forgot-password` | Request password reset |
| POST | `/reset-password` | Reset password |

### Workflows (`/api/workflows`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List workflows |
| POST | `/` | Create workflow |
| GET | `/{id}` | Get workflow |
| PUT | `/{id}` | Update workflow |
| DELETE | `/{id}` | Delete workflow |
| POST | `/{id}/execute` | Execute workflow |
| GET | `/{id}/executions` | Get execution history |
| POST | `/{id}/duplicate` | Duplicate workflow |

### HL7 Messages (`/api/hl7`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/messages` | List messages |
| GET | `/messages/{id}` | Get message |
| POST | `/parse` | Parse HL7 message |
| POST | `/transform` | Transform message |

### AI Workflow (`/api/ai-workflow`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/generate` | Generate workflow from prompt |

### Vendor Endpoints (`/api/vendor-endpoints`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List vendor endpoints |
| POST | `/` | Create endpoint |
| POST | `/ingest` | Ingest message via endpoint |

---

## User Roles Hierarchy

```
SUPER_ADMIN      → Full platform access, can impersonate tenants
    ↓
TENANT_ADMIN     → Manage tenant users, settings, billing
    ↓
WORKFLOW_ADMIN   → Create/edit workflows, manage endpoints
    ↓
ANALYST          → View-only access to dashboards
    ↓
VIEWER           → Read-only access
```

### Auth Dependencies
```python
from api.auth_deps import (
    get_current_user,          # Returns current user dict
    get_current_tenant,        # Returns current tenant dict
    require_super_admin,       # Depends - super admin only
    require_tenant_admin,      # Depends - tenant admin+
    require_workflow_admin,    # Depends - workflow admin+
    require_analyst,           # Depends - analyst+
    verify_api_key,            # Verify API key auth
)
```

---

## Database Tables

### Core Tables
| Table | Description |
|-------|-------------|
| `tenants` | Multi-tenant organizations |
| `users` | User accounts with roles |
| `user_sessions` | Active JWT sessions |
| `workflows` | Workflow definitions |
| `workflow_activities` | Activities within workflows |
| `activity_transformers` | Transformers for activities |
| `workflow_executions` | Execution history |
| `activity_executions` | Per-activity execution logs |

### Message Tables
| Table | Description |
|-------|-------------|
| `hl7_messages` | Stored HL7 messages |
| `vendor_endpoints` | Ingestion endpoints |
| `interop_messages` | Interoperability messages |
| `message_quarantine` | Quarantined bad messages |
| `dlq_messages` | Dead letter queue |

### Billing & Admin
| Table | Description |
|-------|-------------|
| `subscription_plans` | Available plans |
| `billing_invoices` | Billing records |
| `audit_log` | Security audit trail |

---

## Development Workflows

### Creating a New Activity

1. **Create Processor Function** in `processors/`
```python
# processors/my_processor.py
from models.workflow_models import ActivityResult, ActivityStatus, WorkflowContext

async def process_my_activity(activity: dict, context: WorkflowContext) -> ActivityResult:
    try:
        config = activity.get('config', {})
        # Your processing logic here
        
        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={"result": "success"},
            variables={"my_output": "value"}
        )
    except Exception as e:
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message=str(e)
        )
```

2. **Register in Execution Service** (`services/workflow_execution_service.py`)
```python
from processors.my_processor import process_my_activity

# In _register_activity_processors() method:
"my_activity": process_my_activity,
```

3. **Add to AI Knowledge** (`services/ai_service.py`)
```python
# In _get_activity_knowledge() method:
"my_activity": {
    "description": "Description of what this activity does",
    "config_schema": {
        "param1": "default_value",
        "required_field": True
    }
},
```

### Creating a New API Router

1. **Create Router File** in `api/`
```python
# api/my_router.py
from fastapi import APIRouter, Depends
from api.auth_deps import get_current_user, get_current_tenant

router = APIRouter(prefix="/api/my-feature", tags=["my-feature"])

@router.get("/")
async def list_items(
    current_user: dict = Depends(get_current_user),
    tenant: dict = Depends(get_current_tenant)
):
    return {"items": []}
```

2. **Register in server.py**
```python
from api.my_router import router as my_router
app.include_router(my_router)
```

### Adding a Database Model

1. **Create Model** in `models/`
```python
# models/my_model.py
from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from .database import Base
import uuid

class MyModel(Base):
    __tablename__ = "my_models"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"))
    name = Column(String(200), nullable=False)
```

2. **Create Migration**
```bash
alembic revision -m "add my_models table"
# Edit generated migration file
alembic upgrade head
```

---

## Activity Types Reference

### Core Processing
| Type | Description |
|------|-------------|
| `hl7_parser` | Parse raw HL7 to structured data |
| `hl7_transformer` | Transform HL7 fields |
| `filter` | Stop workflow based on conditions |
| `transform` | Modify variables |
| `validation` | Validate data against rules |
| `condition` | If/else branching |
| `loop` | Iterate over lists |
| `segment_loop` | Iterate over HL7 segments |
| `delay` | Wait specified time |
| `custom_code` | Execute Python code (sandboxed) |

### Data Conversion
| Type | Description |
|------|-------------|
| `hl7_to_fhir` | Convert HL7 v2 → FHIR R4 |
| `hl7_to_csv` | Extract HL7 → CSV |
| `csv_to_hl7` | Convert CSV → HL7 |
| `json_converter` | Convert to JSON |
| `xml_converter` | Convert to XML |
| `pipe_converter` | Convert to pipe-delimited |
| `format_converter` | Generic format conversion |
| `data_mapper` | Map fields between formats |

### Storage & Output
| Type | Description |
|------|-------------|
| `s3_storage` | Save to AWS S3 |
| `gcs_storage` | Save to Google Cloud Storage |
| `database_write` | Execute SQL INSERT/UPDATE |
| `file_writer` | Write to filesystem/SFTP |
| `csv_batcher` | Batch rows into CSV files |
| `bigquery_load` | Load into BigQuery |

### Communication
| Type | Description |
|------|-------------|
| `http_sender` | Send HTTP POST/PUT |
| `tcp_sender` | Send via TCP/MLLP |
| `email_sender` | Send email notifications |
| `sqs_producer` | Send to AWS SQS |
| `sqs_consumer` | Read from AWS SQS |
| `databricks_sender` | Send to Databricks |

### EMR Integration
| Type | Description |
|------|-------------|
| `epic_hl7_sender` | Send HL7 to Epic |
| `cerner_fhir_sender` | Send FHIR to Oracle Health |
| `ecw_fhir_sender` | Send FHIR to eClinicalWorks |
| `nextgen_api_sender` | Send to NextGen API |
| `icare_sender` | Send to iCare |

### Healthcare Interoperability
| Type | Description |
|------|-------------|
| `fhir_parser` | Parse FHIR resources |
| `fhir_transformer` | Transform FHIR |
| `fhir_sender` | Send FHIR to server |
| `dicom_parser` | Parse DICOM |
| `dicom_sender` | Send DICOM images |
| `x12_parser` | Parse X12 EDI |
| `x12_sender` | Send X12 transactions |
| `ncpdp_parser` | Parse NCPDP pharmacy |
| `ncpdp_sender` | Send NCPDP data |
| `cda_parser` | Parse CDA documents |
| `cda_sender` | Send CDA documents |
| `ccd_parser` | Parse CCD documents |
| `ccr_parser` | Parse CCR documents |

### Terminology
| Type | Description |
|------|-------------|
| `terminology_lookup` | Look up code meanings |
| `terminology_mapper` | Map between code systems |
| `terminology_translator` | Translate terminology |

---

## Security Middleware

### Enabled Middleware (in order)
1. **SecurityHeadersMiddleware** - Adds security headers (CSP, X-Frame-Options, etc.)
2. **RateLimitMiddleware** - 60 req/min, 1000 req/hour per IP
3. **AuditLogMiddleware** - HIPAA compliance logging
4. **InputValidationMiddleware** - 50MB max request size

### Security Headers Added
```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; ...
Strict-Transport-Security: max-age=31536000 (HTTPS only)
```

---

## Troubleshooting

### Common Issues

**Migration Error**
```bash
# Check current state
alembic current
# If corrupt, in dev only:
# Delete alembic_version table and re-run migrations
```

**Rate Limit (429)**
- Default: 60 requests/minute, 1000/hour
- Health endpoints are exempt

**JWT Token Issues**
```python
# Check token expiry
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30  # Default
# Refresh tokens before expiry
POST /api/auth/refresh
```

**Database Connection Pooling**
- Max pool size: 10 connections
- Pool timeout: 30 seconds
- Check `database/connection.py`

### Database Queries
```sql
-- Recent failed executions
SELECT id, workflow_id, status, error_message, created_at
FROM workflow_executions 
WHERE status = 'FAILED' 
ORDER BY created_at DESC LIMIT 10;

-- Check DLQ
SELECT * FROM dlq_messages 
WHERE tenant_id = 'your-tenant-id' 
ORDER BY created_at DESC;

-- Tenant lookup
SELECT * FROM tenants WHERE slug = 'your-slug';
```

### Logs
```bash
# Docker logs
docker-compose logs -f backend

# Local logs (if configured)
tail -f backend/logs/app.log

# Filter by level
LOG_LEVEL=DEBUG uvicorn server:app --reload
```

---

## Testing

### Run Tests
```bash
pytest                      # All tests
pytest -v                   # Verbose
pytest -k "test_auth"       # Specific tests
pytest --cov=.              # With coverage
```

### Manual API Testing
```bash
# Get auth token
TOKEN=$(curl -s -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"password"}' \
  | jq -r '.access_token')

# Authenticated request
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8001/api/workflows
```

### WebSocket Testing
```javascript
const ws = new WebSocket('ws://localhost:8001/ws');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```
