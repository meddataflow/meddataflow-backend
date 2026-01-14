# meddataflow Backend

## Project Overview
meddataflow is a modern HL7 message integration platform designed to streamline healthcare data workflows. It provides intuitive English translation of HL7 messages, a visual workflow designer, and robust integration capabilities with EMRs and other healthcare systems.

## Tech Stack
- **Language**: Python 3.11+
- **Framework**: FastAPI
- **Database**: PostgreSQL (with asyncpg)
- **ORM**: SQLAlchemy (Async)
- **Migrations**: Alembic
- **Task Queue**: Internal asyncio-based queue service
- **Containerization**: Docker

## Architecture & Data Flow
1.  **Ingestion**: HL7 messages enter via MLLP (TCP) or HTTP.
2.  **Parsing**: Messages are parsed into structured data (JSON/Dictionary) by `HL7Parser`.
3.  **Workflow Execution**: The `WorkflowExecutionService` triggers the appropriate workflow based on message type and tenant.
4.  **Activities**: The workflow executes a sequence of "Activities" (e.g., Filter, Transform, Save to DB, Send to API).
5.  **Output**: Processed data is sent to downstream systems (EMR, Data Lake, SFTP).

## Key Concepts
- **Tenant**: A logical isolation for a customer or organization. Data and workflows are scoped to a tenant.
- **Workflow**: A defined sequence of steps (Activities) to process a specific type of message.
- **Activity**: A single unit of work (e.g., "Parse HL7", "Send Email").
- **Execution**: A single run of a workflow for a specific message.
- **Trigger**: The event that starts a workflow (usually an incoming message).

## Core Services
- **`ai_service`**: Generates workflow configurations from natural language prompts using LLMs.
- **`hl7_parser`**: Robust parser for HL7 v2.x messages, handling various encoding characters and segment structures.
- **`workflow_execution_service`**: The engine that orchestrates activity execution, handles state, and manages retries/errors.
- **`mllp_service`**: Manages TCP/IP connections for HL7 MLLP protocol.

## Prerequisites
- Python 3.11 or higher
- PostgreSQL 14+
- Docker & Docker Compose (optional, for containerized run)

## Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd backend
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   # Windows
   .\venv\Scripts\activate
   # Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration**
   Copy the example environment file and configure it:
   ```bash
   cp .env.example .env
   ```
   Update `.env` with your database credentials and other settings.

## Running the Application

### Local Development
To run the server locally with hot-reload enabled:
```bash
uvicorn server:app --reload --port 8001
```
The API will be available at `http://localhost:8001`.

### Docker
To run using Docker Compose:
```bash
docker-compose up --build
```

### Startup Script
The `startup.sh` script handles database initialization (waiting for DB readiness, running migrations) before starting the server.
```bash
./startup.sh
```

## Project Structure
- `api/`: FastAPI routers and endpoints.
- `services/`: Business logic and core services (HL7 parsing, AI, Workflow execution).
- `models/`: SQLAlchemy database models and Pydantic schemas.
- `database/`: Database connection and session management.
- `processors/`: Activity processors for workflow steps.
- `migrations/`: Alembic migration scripts.
- `config/`: Configuration files.

## API Documentation
Once the server is running, you can access the interactive API documentation:
- **Swagger UI**: [http://localhost:8001/docs](http://localhost:8001/docs)
- **ReDoc**: [http://localhost:8001/redoc](http://localhost:8001/redoc)

## Testing
(Add testing instructions here when tests are implemented, e.g., `pytest`)
