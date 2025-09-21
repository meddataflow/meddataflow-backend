"""
Database initialization and seeding for meddataflow platform
"""
import asyncio
import uuid
import json
from datetime import datetime, timezone

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import create_connection_pool, close_connection_pool
from database.schema import create_tables, drop_tables
from models.tenant import TenantRepository, TenantPlan
from models.user import UserRepository, UserRole
from models.vendor_endpoint import VendorEndpointRepository
from models.workflow import WorkflowRepository
from models.hl7_message import HL7MessageRepository

async def init_database():
    """Initialize database with schema"""
    print("🔄 Initializing database...")
    
    try:
        # Connect to database
        await create_connection_pool()
        print("✅ Database connection established")
        
        # Create tables
        await create_tables()
        print("✅ Database schema created")
        
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        raise
    finally:
        await close_connection_pool()

async def seed_database():
    """Seed database with initial data"""
    print("🔄 Seeding database with initial data...")
    
    try:
        # Connect to database
        await create_connection_pool()
        
        # Create default tenant
        print("Creating default tenant...")
        tenant = await TenantRepository.create_tenant(
            name="meddataflow Demo",
            slug="demo",
            plan=TenantPlan.PROFESSIONAL,
            billing_email="admin@meddataflow.com"
        )
        tenant_id = tenant['id'] if isinstance(tenant['id'], uuid.UUID) else uuid.UUID(tenant['id'])
        print(f"✅ Default tenant created: {tenant['name']}")
        
        # Create admin user
        print("Creating admin user...")
        admin_user = await UserRepository.create_user(
            email="admin@demo.com",
            password="admin123",
            first_name="Admin",
            last_name="Demo",
            tenant_id=tenant_id,
            role=UserRole.TENANT_ADMIN
        )
        admin_user_id = admin_user['id'] if isinstance(admin_user['id'], uuid.UUID) else uuid.UUID(admin_user['id'])
        print(f"✅ Admin user created: {admin_user['email']}")
        
        # Create workflow admin user
        print("Creating workflow admin user...")
        workflow_user = await UserRepository.create_user(
            email="workflows@demo.com",
            password="demo123",
            first_name="Workflow",
            last_name="Admin",
            tenant_id=tenant_id,
            role=UserRole.WORKFLOW_ADMIN
        )
        print(f"✅ Workflow admin user created: {workflow_user['email']}")
        
        # Create analyst user
        print("Creating analyst user...")
        analyst_user = await UserRepository.create_user(
            email="analyst@demo.com",
            password="demo123",
            first_name="Data",
            last_name="Analyst",
            tenant_id=tenant_id,
            role=UserRole.ANALYST
        )
        print(f"✅ Analyst user created: {analyst_user['email']}")

        # Create super admin user (no tenant)
        print("Creating super admin user...")
        super_user = await UserRepository.create_user(
            email="superadmin@demo.com",
            password="superadmin123",
            first_name="Super",
            last_name="Admin",
            tenant_id=None,  # platform-level super admin
            role=UserRole.SUPER_ADMIN
        )
        print(f"✅ Super admin user created: {super_user['email']}")
        
        # Create sample vendor endpoint
        print("Creating sample vendor endpoint...")
        vendor_endpoint = await VendorEndpointRepository.create_endpoint(
            tenant_id=tenant_id,
            vendor_slug="epic",
            vendor_name="Epic Healthcare",
            vendor_description="Epic EHR system integration endpoint",
            vendor_contact_email="integration@epic.com",
            api_key=str(uuid.uuid4()),
            message_format="hl7",
            max_message_size=5242880,  # 5MB
            rate_limit_per_hour=5000
        )
        vendor_endpoint_id = vendor_endpoint['id'] if isinstance(vendor_endpoint['id'], uuid.UUID) else uuid.UUID(vendor_endpoint['id'])
        print(f"✅ Sample vendor endpoint created: {vendor_endpoint['vendor_name']}")
        
        # Create sample workflow
        print("Creating sample workflow...")
        workflow = await WorkflowRepository.create_workflow(
            tenant_id=tenant_id,
            created_by_id=admin_user_id,
            name="Patient Registration Workflow",
            description="Process incoming patient registration messages from Epic"
        )
        workflow_id = workflow['id'] if isinstance(workflow['id'], uuid.UUID) else uuid.UUID(workflow['id'])
        print(f"✅ Sample workflow created: {workflow['name']}")
        
        # Create sample HL7 messages
        print("Creating sample HL7 messages...")
        
        sample_hl7_messages = [
            {
                "raw_message": """MSH|^~\\&|EPIC|EPICADT|SMS|SMSADT|199912271408|CHARRIS|ADT^A04|1817457|D|2.5||
EVN||199912271408||199912271408
PID|0001|0000112234^^^MR|0000112234^^^MR~444333333^^^SSN||EVERYMAN^ADAM||19661003|M||C|1200 N ELM STREET^^GREENSBORO^NC^27401-1020|GL|(919)379-1212|(919)271-3434||S||PATID12345001^2^M10|123456789|9-87654^NC||
NK1|0001|JONES^BARBARA^K|12|171 ZOBERLEIN^^GREENSBORO^NC^27410-1020|(919)349-2000|(919)332-1800||||||||||||||||||||||||||||
PV1|0001|I|2000^2012^01||||004777^ATTEND^AARON^A|||SUR|||A0||19|1
AL1|1||^PENICILLIN^L|MO|SHORTNESS OF BREATH
DG1|001|I9|^784.0^I9|SHORTNESS OF BREATH||A""",
                "message_type": "ADT^A04",
                "event_type": "A04",
                "hl7_version": "2.5",
                "sending_application": "EPIC",
                "receiving_application": "SMS"
            },
            {
                "raw_message": """MSH|^~\\&|CERNER|CERNERADT|SMS|SMSADT|199912271409|CHARRIS|ORM^O01|1817458|D|2.5||
PID|0001|0000112235^^^MR|0000112235^^^MR~555444444^^^SSN||DOE^JANE||19801215|F||C|300 MAIN STREET^^CHARLOTTE^NC^28202-1010|GL|(704)555-1234|(704)555-5678||M||PATID12345002^2^M10|987654321|9-12345^NC||
ORC|NW|12345^CERNER|67890^SMS||CM||||199912271409|^SMITH^JOHN^A||^DOCTOR^ATTENDING
OBR|1|12345^CERNER|67890^SMS|^CBC^CBC with Differential||199912271409|||^SMITH^JOHN^A||||||199912271409|S||^SMITH^JOHN^A||||||||F""",
                "message_type": "ORM^O01",
                "event_type": "O01",
                "hl7_version": "2.5",
                "sending_application": "CERNER",
                "receiving_application": "SMS"
            }
        ]
        
        for i, msg_data in enumerate(sample_hl7_messages):
            message = await HL7MessageRepository.create_message(
                tenant_id=tenant_id,
                created_by_id=admin_user_id if i == 0 else (analyst_user['id'] if isinstance(analyst_user['id'], uuid.UUID) else uuid.UUID(analyst_user['id'])),
                workflow_id=workflow_id,
                vendor_endpoint_id=vendor_endpoint_id,
                raw_message=msg_data["raw_message"],
                message_type=msg_data["message_type"],
                event_type=msg_data["event_type"],
                hl7_version=msg_data["hl7_version"],
                sending_application=msg_data["sending_application"],
                receiving_application=msg_data["receiving_application"],
                parsed_message=json.dumps({
                    "segments": ["MSH", "EVN", "PID", "PV1"] if i == 0 else ["MSH", "PID", "ORC", "OBR"],
                    "patient_id": "0000112234" if i == 0 else "0000112235",
                    "message_control_id": "1817457" if i == 0 else "1817458"
                }),
                english_translation=json.dumps({
                    "summary": f"Patient {'admission' if i == 0 else 'order'} message",
                    "details": [
                        f"Patient {'Adam Everyman' if i == 0 else 'Jane Doe'} message received",
                        f"Message type: {msg_data['message_type']}",
                        f"From: {msg_data['sending_application']}"
                    ]
                })
            )
            print(f"✅ Sample message {i+1} created: {message['message_type']}")
        
        print("🎉 Database seeding completed successfully!")
        
        # Print summary
        print("\n📊 Summary:")
        print(f"   • Tenant: {tenant['name']} ({tenant['slug']})")
        print(f"   • API Key: {tenant['api_key']}")
        print(f"   • Admin: admin@demo.com / admin123")
        print(f"   • Super Admin: superadmin@demo.com / superadmin123")
        print(f"   • Workflow Admin: workflows@demo.com / demo123")
        print(f"   • Analyst: analyst@demo.com / demo123")
        print(f"   • Vendor Endpoint: {vendor_endpoint['vendor_name']}")
        print(f"   • Sample Workflow: {workflow['name']}")
        print(f"   • Sample Messages: {len(sample_hl7_messages)}")
        
    except Exception as e:
        print(f"❌ Database seeding failed: {e}")
        raise
    finally:
        await close_connection_pool()

async def reset_database():
    """Reset database by dropping and recreating all tables"""
    print("🔄 Resetting database...")
    
    try:
        await create_connection_pool()
        
        # Drop all tables
        await drop_tables()
        print("✅ Dropped all tables")
        
        # Recreate tables
        await create_tables()
        print("✅ Recreated database schema")
        
    except Exception as e:
        print(f"❌ Database reset failed: {e}")
        raise
    finally:
        await close_connection_pool()

async def ensure_database():
    """Ensure schema exists and seed only if empty (no tenants)."""
    try:
        await create_connection_pool()
        # Create tables if needed
        await create_tables()
        # Check if any tenant exists
        tenants = await TenantRepository.get_all_tenants()
        if tenants:
            print("✅ Database already initialized; skipping seeding")
            return
    finally:
        await close_connection_pool()

    # If no tenants, run full seed
    await seed_database()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "init":
            asyncio.run(init_database())
        elif command == "seed":
            asyncio.run(seed_database())
        elif command == "reset":
            asyncio.run(reset_database())
        elif command == "full":
            asyncio.run(reset_database())
            asyncio.run(seed_database())
        elif command == "ensure":
            asyncio.run(ensure_database())
        else:
            print("Usage: python init_db.py [init|seed|reset|full]")
            print("  init  - Create database tables")
            print("  seed  - Add sample data")
            print("  reset - Drop and recreate tables")
            print("  full  - Reset and seed database")
            print("  ensure- Create tables and seed only if empty")
    else:
        # Default: full reset and seed
        asyncio.run(reset_database())
        asyncio.run(seed_database())
