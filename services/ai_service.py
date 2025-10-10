"""
AI Service for OpenRouter integration
Handles AI-powered workflow generation from user prompts
"""
import json
import logging
import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import uuid

from models.workflow import ActivityType, TransformerType, MessageFormat

logger = logging.getLogger(__name__)


class AIWorkflowService:
    """Service for AI-powered workflow generation using OpenRouter"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.base_url = "https://openrouter.ai/api/v1"
        self.model = "anthropic/claude-3.5-sonnet"  # High-quality model for complex reasoning

    def set_api_key(self, api_key: str):
        """Update the API key"""
        self.api_key = api_key

    async def generate_workflow_from_prompt(
        self,
        user_prompt: str,
        tenant_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        Generate a complete workflow configuration from user prompt
        """
        if not self.api_key:
            raise ValueError("AI API key not configured")

        try:
            # Prepare the system prompt with activity knowledge
            system_prompt = self._build_system_prompt()

            # Create the user message
            user_message = f"""
            Create a workflow for the following requirement:

            "{user_prompt}"

            Please generate a complete workflow configuration with activities in the correct order.
            Make sure to include proper activity configurations and transformers where needed.
            """

            # Call OpenRouter API
            response_data = await self._call_openrouter_api(system_prompt, user_message)

            # Parse and validate the AI response
            workflow_config = self._parse_ai_response(response_data)

            # Add metadata
            workflow_config.update({
                "generated_by_ai": True,
                "ai_prompt": user_prompt,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tenant_id": tenant_id,
                "created_by_id": user_id
            })

            return workflow_config

        except Exception as e:
            logger.error(f"Error generating workflow from AI: {e}")
            raise Exception(f"Failed to generate workflow: {str(e)}")

    def _build_system_prompt(self) -> str:
        """Build comprehensive system prompt with activity knowledge"""

        # Get all available activity types and their configurations
        activity_knowledge = self._get_activity_knowledge()

        return f"""
You are an expert HL7 workflow designer. Your job is to create workflow configurations based on user requirements.

AVAILABLE ACTIVITY TYPES AND CONFIGURATIONS:
{json.dumps(activity_knowledge, indent=2)}

WORKFLOW STRUCTURE:
- A workflow consists of multiple activities executed in sequence
- Each activity has a type, name, configuration, and optional transformers
- Activities process HL7 messages and can extract variables, transform data, and output to various destinations

RESPONSE FORMAT:
You MUST respond with ONLY a valid JSON object. Do not include any text before or after the JSON.
Do not wrap the JSON in code blocks or markdown.
Do not provide explanations.

Return ONLY this JSON structure:
{{
  "name": "Descriptive workflow name",
  "description": "Clear description of what this workflow does",
  "version": "1.0.0",
  "activities": [
    {{
      "name": "Activity name",
      "activity_type": "activity_type_from_enum",
      "order": 1,
      "config": {{
        // Activity-specific configuration
      }},
      "transformers": [
        {{
          "name": "Transformer name",
          "transformer_type": "transformer_type_from_enum",
          "order": 1,
          // Additional transformer fields based on type
        }}
      ]
    }}
  ]
}}

IMPORTANT RULES:
1. Always start with HL7 parsing if working with HL7 messages
2. Use appropriate activity types from the available list
3. Include proper field mappings and transformations
4. Ensure activities are in logical execution order
5. Include error handling where appropriate
6. Use realistic configuration values
7. Add transformers for data extraction and mapping

COMMON PATTERNS:
1. HL7 Processing: hl7_parser → filter → transform → output
2. Data Export: hl7_parser → csv_converter → s3_storage
3. Integration: hl7_parser → transform → http_sender
4. Validation: hl7_parser → validation → conditional processing

Be specific with configurations and make sure all required fields are included.
"""

    def _get_activity_knowledge(self) -> Dict[str, Any]:
        """Get comprehensive knowledge about available activities"""
        return {
            "activity_types": {
                "hl7_parser": {
                    "description": "Parse HL7 messages and extract variables; can generate readable text",
                    "config_schema": {
                        "variables": [{"name": "SENDING_APPLICATION", "source": "MSH.3", "default": ""}],
                        "readable_format": True
                    }
                },
                "hl7_transformer": {
                    "description": "Transform HL7 to another HL7 format using mapping rules",
                    "config_schema": {"mappings": [{"source": "MSH.3", "target": "ZPF.1", "transform": "direct"}]}
                },
                "hl7_to_fhir": {
                    "description": "Convert HL7 to FHIR Bundle with Patient (and optional Encounter)",
                    "config_schema": {
                        "resource_type": "Patient",
                        "mappings": {"gender": {"segment": "PID", "field": 8, "transform": "gender_mapping"}}
                    }
                },
                "hl7_to_csv": {
                    "description": "Convert HL7 to CSV using headers and field mappings",
                    "config_schema": {
                        "headers": ["patient_id", "last_name", "first_name"],
                        "mappings": {"patient_id": {"source_location": "PID.3", "default_value": ""}},
                        "csv_headers": ["patient_id"],
                        "field_mappings": "{\"patient_id\": \"PID.3\"}"
                    }
                },
                "segment_loop": {
                    "description": "Loop through HL7 segments or field values and optionally run nested actions",
                    "config_schema": {
                        "segment_name": "OBX",
                        "mode": "each-segment",
                        "hl7_target": "OBX.5",
                        "variable_name": "loop_item",
                        "index_variable": "loop_index",
                        "max_iterations": 100,
                        "actions": [{"type": "set_variable", "variable": "LAST_OBX", "value": "{{loop_item}}"}],
                        "nested_activities": []
                    }
                },

                "filter": {
                    "description": "Filter messages based on variable conditions",
                    "config_schema": {
                        "conditions": [{"variable": "SENDING_APPLICATION", "operator": "equals", "value": "EPIC"}],
                        "logical_operator": "AND"
                    }
                },
                "condition": {
                    "description": "Conditional branching; supports single or multi-condition with actions",
                    "config_schema": {
                        "condition_variable": "PATIENT_AGE",
                        "condition_operator": "greater_than",
                        "condition_value": 65,
                        "on_true": "continue",
                        "on_false": "skip",
                        "conditions": [{"variable": "MESSAGE_TYPE", "operator": "equals", "value": "ADT^A01", "action": "set_path", "action_config": {"path": "admit"}}],
                        "default_action": {"action": "continue"},
                        "extract_from_message": {"PATIENT_LAST_NAME": {"segment": "PID", "field": 5}}
                    }
                },
                "delay": {
                    "description": "Delay next activity execution",
                    "config_schema": {"delay_seconds": 1, "max_delay_seconds": 300, "reason": "Processing delay"}
                },
                "loop": {
                    "description": "Generic loop over arrays, HL7 items, or repeats",
                    "config_schema": {
                        "mode": "each",
                        "source": "variable:items",
                        "hl7_target": "OBX.5",
                        "repeat_count": 3,
                        "variable_name": "loop_item",
                        "index_variable": "loop_index",
                        "max_iterations": 100,
                        "actions": [{"type": "set_variable", "variable": "COLLECTED", "value": "{{loop_item}}"}]
                    }
                },

                "transform": {
                    "description": "Transform variables to new structure (non-HL7)",
                    "config_schema": {
                        "input_format": "HL7v2",
                        "output_format": "JSON",
                        "mappings": [{"source": "PATIENT_NAME", "target": "patient_name", "transform": "direct"}]
                    }
                },
                "csv_converter": {
                    "description": "Convert variables to CSV content",
                    "config_schema": {
                        "fields": ["patient_id", "last_name", "first_name"],
                        "delimiter": ",",
                        "include_headers": True
                    }
                },
                "json_converter": {"description": "Convert to JSON (mock)", "config_schema": {}},
                "xml_converter": {"description": "Convert to XML (mock)", "config_schema": {}},
                "pipe_separated_converter": {"description": "Convert to pipe-separated (mock)", "config_schema": {}},
                "pipe_converter": {"description": "Alias of pipe_separated_converter", "config_schema": {}},

                "s3_storage": {
                    "description": "Store CSV/JSON content to S3",
                    "config_schema": {
                        "bucket": {"name": "hl7-processed-files", "key_prefix": "{tenant_id}/{date}/", "file_pattern": "{filename}"},
                        "key_pattern": "{tenant_id}/{date}/{filename}",
                        "encryption": True
                    }
                },
                "csv_batcher": {
                    "description": "Buffer CSV rows and flush to S3 when thresholds are met (reduces one-file-per-message)",
                    "config_schema": {
                        "group_key_template": "hl7/{tenant_id}/{date}/workflow-{workflow_id}.csv",
                        "thresholds": {"max_rows": 1000, "max_age_seconds": 300, "max_bytes": 5000000},
                        "s3": {"bucket": "hl7-processed-files", "key_template": None},
                        "dedupe_on_message_id": True
                    }
                },
                "file_writer": {
                    "description": "Write file to local/FTP/SFTP with variable substitution",
                    "config_schema": {
                        "connection": {"protocol": "local", "host": "", "port": 22, "username": "", "password": ""},
                        "file": {"file_path": "/tmp/output.txt", "content": "{{raw}}", "format": "text", "encoding": "utf-8"},
                        "variables": [],
                        "substitute_vars": True
                    }
                },
                "http_sender": {
                    "description": "Send HTTP request with selected variables or custom payload",
                    "config_schema": {
                        "request": {
                            "url": "https://api.example.com/webhook",
                            "method": "POST",
                            "headers": {"Content-Type": "application/json"},
                            "timeout": 30,
                            "auth_type": "none",
                            "auth": {"username": "", "password": "", "token": ""}
                        },
                        "data": {"selected_variables": ["PATIENT_ID"], "format": "json", "custom_payload": ""},
                        "headers": {},
                        "variables": []
                    }
                },
                "email_sender": {
                    "description": "Send email via SMTP",
                    "config_schema": {
                        "smtp": {"server": "smtp.example.com", "port": 587, "username": "", "password": "", "use_tls": True},
                        "to": ["alerts@hospital.com"],
                        "subject": "HL7 processed: {MESSAGE_CONTROL_ID}",
                        "body_template": "Patient {PATIENT_LAST_NAME}, {PATIENT_FIRST_NAME}"
                    }
                },
                "database_write": {
                    "description": "Execute parameterized SQL against DB; placeholders {VAR} pulled from variables",
                    "config_schema": {
                        "database_type": "postgresql",
                        "connection": {"host": "localhost", "port": 5432, "database": "hl7", "username": "user", "password": "pass"},
                        "query_config": {"query": "INSERT INTO messages(id, payload) VALUES({MESSAGE_CONTROL_ID}, {raw})", "query_type": "insert", "table_name": "messages"}
                    }
                },
                "databricks_sender": {
                    "description": "Insert HL7 payload into Databricks table via SQL Warehouse or REST",
                    "config_schema": {
                        "connection": {"workspace_url": "https://<workspace>", "http_path": "/sql/1.0/warehouses/<id>", "access_token": "", "catalog": "hl7", "schema": "hl7", "table": "hl7_message"},
                        "data": {"database": "hl7", "target_table": "hl7_message"},
                        "sql": {"pre_insert": "", "post_insert": ""}
                    }
                },
                "tcp_sender": {
                    "description": "Send raw/transformed HL7 via TCP/MLLP (optional ACK)",
                    "config_schema": {
                        "host": "localhost", "port": 1080, "connection_timeout": 5.0, "read_timeout": 30.0,
                        "use_ssl": False, "use_mllp": False, "expect_ack": False, "ack_timeout_ms": 5000
                    }
                },

                "message_transformer": {
                    "description": "Map values from HL7 paths to new keys with simple transformations",
                    "config_schema": {"transformation_rules": [{"source_path": "PID.5.1", "target_path": "patient.last_name", "transformation": "uppercase"}], "output_format": "json"}
                },
                "format_converter": {
                    "description": "Convert data formats (mock)",
                    "config_schema": {"input_format": "HL7", "output_format": "JSON"}
                },
                "data_mapper": {
                    "description": "Apply generic field mappings (mock)",
                    "config_schema": {"mappings": [{"source": "A", "target": "B"}]}
                },

                "custom_code": {
                    "description": "Execute sandboxed Python code; uses context variables",
                    "config_schema": {
                        "code": "# context_vars (dict) and result_vars (dict) are available\nresult_vars['ok']=True",
                        "language": "python",
                        "input_variables": ["PATIENT_ID"],
                        "output_variables": ["computed_value"],
                        "allowed_imports": ["math", "json", "datetime"],
                        "timeout_seconds": 30,
                        "sandbox_mode": True
                    }
                },
                "validation": {
                    "description": "Validate variables or HL7 fields against rules",
                    "config_schema": {
                        "validation_rules": [
                            {"value_source": "variable", "variable_name": "PATIENT_ID", "validation_type": "required"},
                            {"value_source": "message", "field_path": "PID.3", "validation_type": "regex", "expected_value": "^[0-9]+$"}
                        ]
                    }
                },

                "fhir_parser": {
                    "description": "Parse FHIR resources and extract fields into workflow variables",
                    "config_schema": {
                        "input_variable": "fhir_payload",
                        "extraction_rules": [
                            {"name": "FHIR_ID", "path": "id"},
                            {"name": "FHIR_RESOURCE_TYPE", "path": "resourceType"}
                        ],
                        "store_parsed_as": "fhir_resource"
                    }
                },
                "fhir_transformer": {
                    "description": "Apply transformation rules to FHIR resources",
                    "config_schema": {
                        "input_variable": "fhir_resource",
                        "output_variable": "fhir_transformed",
                        "transformation_rules": [
                            {"source_path": "name[0].family", "target_path": "patient.last_name", "operation": "copy"},
                            {"operation": "set", "target_path": "meta.profile[0]", "value": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"}
                        ]
                    }
                },
                "fhir_translator": {
                    "description": "Translate FHIR resources into alternative formats (e.g., HL7v2, JSON)",
                    "config_schema": {
                        "input_variable": "fhir_transformed",
                        "target_format": "hl7v2",
                        "store_result_as": "fhir_hl7v2",
                        "translation_mappings": {}
                    }
                },
                "fhir_sender": {
                    "description": "Send FHIR payloads to RESTful endpoints",
                    "config_schema": {
                        "payload_variable": "fhir_transformed",
                        "endpoint_url": "https://ehr.example.com/fhir",
                        "transport_protocol": "https",
                        "simulate": True
                    }
                },

                "dicom_parser": {
                    "description": "Parse DICOM payloads (binary or metadata) and extract summary details",
                    "config_schema": {
                        "input_variable": "dicom_payload",
                        "store_parsed_as": "dicom_metadata"
                    }
                },
                "dicom_transformer": {
                    "description": "Apply transformation rules to DICOM metadata",
                    "config_schema": {
                        "input_variable": "dicom_metadata",
                        "output_variable": "dicom_transformed_metadata",
                        "transformation_rules": [
                            {"source_path": "PatientName", "target_path": "patient.name", "operation": "copy"}
                        ]
                    }
                },
                "dicom_translator": {
                    "description": "Translate DICOM metadata into FHIR ImagingStudy or DiagnosticReport",
                    "config_schema": {
                        "input_variable": "dicom_transformed_metadata",
                        "target_format": "imagingstudy",
                        "store_result_as": "dicom_imaging_study"
                    }
                },
                "dicom_sender": {
                    "description": "Send DICOM payloads to PACS/VNA endpoints (simulated by default)",
                    "config_schema": {
                        "payload_variable": "dicom_payload",
                        "endpoint_url": "dicom://pacs.example.com",
                        "transport_protocol": "dicom",
                        "simulate": True
                    }
                },

                "ncpdp_parser": {
                    "description": "Parse NCPDP SCRIPT/Telecom messages into key/value pairs",
                    "config_schema": {
                        "input_variable": "ncpdp_payload",
                        "store_parsed_as": "ncpdp_message"
                    }
                },
                "ncpdp_transformer": {
                    "description": "Transform NCPDP fields using mapping rules",
                    "config_schema": {
                        "input_variable": "ncpdp_message",
                        "output_variable": "ncpdp_transformed",
                        "transformation_rules": [
                            {"source_path": "ABA", "target_path": "pharmacy.id", "operation": "copy"}
                        ]
                    }
                },
                "ncpdp_translator": {
                    "description": "Translate NCPDP message into JSON or other interchange formats",
                    "config_schema": {
                        "input_variable": "ncpdp_transformed",
                        "target_format": "json",
                        "store_result_as": "ncpdp_json"
                    }
                },
                "ncpdp_sender": {
                    "description": "Send NCPDP payload to external switch or PBM endpoints",
                    "config_schema": {
                        "payload_variable": "ncpdp_transformed",
                        "endpoint_url": "tcp://switch.example.com:5000",
                        "transport_protocol": "tcp",
                        "simulate": True
                    }
                },

                "x12_parser": {
                    "description": "Parse X12 EDI payloads into segment dictionary",
                    "config_schema": {
                        "input_variable": "x12_payload",
                        "store_parsed_as": "x12_message"
                    }
                },
                "x12_transformer": {
                    "description": "Transform X12 segments using mapping rules",
                    "config_schema": {
                        "input_variable": "x12_message",
                        "output_variable": "x12_transformed",
                        "transformation_rules": [
                            {"source_path": "ISA[0][5]", "target_path": "sender_id", "operation": "copy"}
                        ]
                    }
                },
                "x12_translator": {
                    "description": "Translate X12 payload into JSON or flat-file representations",
                    "config_schema": {
                        "input_variable": "x12_transformed",
                        "target_format": "json",
                        "store_result_as": "x12_json"
                    }
                },
                "x12_sender": {
                    "description": "Send X12 payloads through SFTP/AS2 gateways (simulated by default)",
                    "config_schema": {
                        "payload_variable": "x12_transformed",
                        "endpoint_url": "sftp://payer.example.com/edi",
                        "transport_protocol": "sftp",
                        "simulate": True
                    }
                },

                "cda_parser": {
                    "description": "Parse CDA documents and extract summary metadata",
                    "config_schema": {
                        "input_variable": "cda_payload",
                        "store_parsed_as": "cda_document"
                    }
                },
                "cda_transformer": {
                    "description": "Apply metadata transformations to CDA documents",
                    "config_schema": {
                        "input_variable": "cda_document",
                        "output_variable": "cda_transformed",
                        "transformation_rules": [
                            {"source_path": "summary.title", "target_path": "metadata.title", "operation": "copy"}
                        ]
                    }
                },
                "cda_translator": {
                    "description": "Translate CDA into JSON bundles or other clinical document formats",
                    "config_schema": {
                        "input_variable": "cda_transformed",
                        "target_format": "json",
                        "store_result_as": "cda_json"
                    }
                },
                "cda_sender": {
                    "description": "Send CDA documents to HIE/Direct endpoints (simulated by default)",
                    "config_schema": {
                        "payload_variable": "cda_document",
                        "endpoint_url": "https://hie.example.com/cda",
                        "transport_protocol": "https",
                        "simulate": True
                    }
                },

                "ccd_parser": {
                    "description": "Parse CCD documents and extract summary metadata",
                    "config_schema": {
                        "input_variable": "ccd_payload",
                        "store_parsed_as": "ccd_document"
                    }
                },
                "ccd_transformer": {
                    "description": "Apply metadata transformations to CCD documents",
                    "config_schema": {
                        "input_variable": "ccd_document",
                        "output_variable": "ccd_transformed",
                        "transformation_rules": []
                    }
                },
                "ccd_translator": {
                    "description": "Translate CCD into alternate formats (JSON, FHIR Composition)",
                    "config_schema": {
                        "input_variable": "ccd_transformed",
                        "target_format": "json",
                        "store_result_as": "ccd_json"
                    }
                },
                "ccd_sender": {
                    "description": "Send CCD payloads to care coordination endpoints (simulated by default)",
                    "config_schema": {
                        "payload_variable": "ccd_document",
                        "endpoint_url": "https://ccd-endpoint.example.com/upload",
                        "transport_protocol": "https",
                        "simulate": True
                    }
                },

                "ccr_parser": {
                    "description": "Parse CCR documents and extract summary metadata",
                    "config_schema": {
                        "input_variable": "ccr_payload",
                        "store_parsed_as": "ccr_document"
                    }
                },
                "ccr_transformer": {
                    "description": "Apply metadata transformations to CCR documents",
                    "config_schema": {
                        "input_variable": "ccr_document",
                        "output_variable": "ccr_transformed",
                        "transformation_rules": []
                    }
                },
                "ccr_translator": {
                    "description": "Translate CCR into alternate formats (JSON, FHIR Composition)",
                    "config_schema": {
                        "input_variable": "ccr_transformed",
                        "target_format": "json",
                        "store_result_as": "ccr_json"
                    }
                },
                "ccr_sender": {
                    "description": "Send CCR payloads to care coordination endpoints (simulated by default)",
                    "config_schema": {
                        "payload_variable": "ccr_document",
                        "endpoint_url": "https://ccr-endpoint.example.com/upload",
                        "transport_protocol": "https",
                        "simulate": True
                    }
                },

                "terminology_lookup": {
                    "description": "Resolve clinical codes (SNOMED CT, ICD, LOINC, etc.) into concepts",
                    "config_schema": {
                        "code_system": "SNOMED",
                        "code": "123456",
                        "store_result_as": "concept"
                    }
                },
                "terminology_mapper": {
                    "description": "Map codes between code systems using lookup tables",
                    "config_schema": {
                        "source_code": "123456",
                        "mapping_table": {"123456": "A01"},
                        "store_result_as": "mapped_code"
                    }
                },
                "terminology_translator": {
                    "description": "Translate codes into target code systems (e.g., SNOMED → ICD-10)",
                    "config_schema": {
                        "source_code": "123456",
                        "target_system": "ICD10",
                        "store_result_as": "icd10_code",
                        "translation_profile": {"123456": "A01.1"}
                    }
                },
                "terminology_publisher": {
                    "description": "Publish code mappings to external terminology services",
                    "config_schema": {
                        "payload_variable": "mapped_code",
                        "endpoint_url": "https://terminology.example.com",
                        "simulate": True
                    }
                }
            },

            "ecw_fhir_sender": {
                "name": "eClinicalWorks FHIR Sender",
                "description": "Send FHIR resources to eClinicalWorks EMR system",
                "category": "EMR Integration",
                "use_cases": [
                    "Send patient data to eClinicalWorks",
                    "Create new patient records in ECW",
                    "Update patient information in ECW",
                    "Send observations and clinical data to ECW"
                ],
                "example_config": {
                    "base_url": "https://fhir.eclinicalworks.com",
                    "oauth_token": "your_oauth_token",
                    "resource_type": "Patient",
                    "operation": "create",
                    "timeout_seconds": 30,
                    "field_mappings": [
                        {"source_field": "PATIENT_ID", "target_field": "identifier.0.value", "default_value": ""},
                        {"source_field": "PATIENT_LAST_NAME", "target_field": "name.0.family", "default_value": ""},
                        {"source_field": "PATIENT_FIRST_NAME", "target_field": "name.0.given.0", "default_value": ""}
                    ]
                }
            },

            "nextgen_api_sender": {
                "name": "NextGen Healthcare API Sender",
                "description": "Send data to NextGen Healthcare via Enterprise APIs",
                "category": "EMR Integration",
                "use_cases": [
                    "Send patient data to NextGen",
                    "Create appointments in NextGen",
                    "Send lab results to NextGen",
                    "Update patient medications in NextGen"
                ],
                "example_config": {
                    "base_url": "https://api.nextgen.com",
                    "api_key": "your_api_key",
                    "endpoint": "/patients",
                    "http_method": "POST",
                    "timeout_seconds": 30,
                    "field_mappings": [
                        {"source_field": "PATIENT_ID", "target_field": "patientId", "default_value": ""},
                        {"source_field": "PATIENT_LAST_NAME", "target_field": "lastName", "default_value": ""},
                        {"source_field": "PATIENT_FIRST_NAME", "target_field": "firstName", "default_value": ""}
                    ],
                    "custom_headers": {
                        "Content-Type": "application/json"
                    }
                }
            },

            "cerner_fhir_sender": {
                "name": "Oracle Health FHIR Sender",
                "description": "Send FHIR R4 resources to Oracle Health (formerly Cerner) EMR system",
                "category": "EMR Integration",
                "use_cases": [
                    "Send patient data to Oracle Health",
                    "Create FHIR observations in Oracle Health",
                    "Update patient encounters in Oracle Health",
                    "Send clinical data to Oracle Health"
                ],
                "example_config": {
                    "base_url": "https://fhir-myrecord.cerner.com/r4/tenant-id",
                    "oauth_token": "your_oauth_token",
                    "resource_type": "Patient",
                    "operation": "create",
                    "timeout_seconds": 30,
                    "field_mappings": [
                        {"source_field": "PATIENT_ID", "target_field": "identifier.0.value", "default_value": ""},
                        {"source_field": "PATIENT_LAST_NAME", "target_field": "name.0.family", "default_value": ""},
                        {"source_field": "PATIENT_FIRST_NAME", "target_field": "name.0.given.0", "default_value": ""},
                        {"source_field": "DATE_OF_BIRTH", "target_field": "birthDate", "default_value": ""}
                    ]
                }
            },

            "epic_hl7_sender": {
                "name": "Epic HL7 Sender",
                "description": "Send HL7 messages to Epic EMR system via inbound interfaces",
                "category": "EMR Integration",
                "use_cases": [
                    "Send patient admission messages (ADT^A01) to Epic",
                    "Send patient registration messages (ADT^A04) to Epic",
                    "Send patient discharge messages (ADT^A03) to Epic",
                    "Send observation results (ORU^R01) to Epic"
                ],
                "example_config": {
                    "hl7_endpoint": "https://epic-interface.hospital.com/hl7",
                    "timeout_seconds": 30,
                    "message_type": "ADT^A04",
                    "sending_application": "MEDDATAFLOW",
                    "receiving_application": "EPIC",
                    "field_mappings": [
                        {"source_field": "PATIENT_ID", "target_field": "PID.3", "default_value": ""},
                        {"source_field": "PATIENT_LAST_NAME", "target_field": "PID.5.1", "default_value": ""},
                        {"source_field": "PATIENT_FIRST_NAME", "target_field": "PID.5.2", "default_value": ""},
                        {"source_field": "DATE_OF_BIRTH", "target_field": "PID.7", "default_value": ""}
                    ]
                }
            },

            "transformer_types": {
                "variable": {"description": "Extract and store variable", "fields": ["variable_name", "source_path", "default_value"]},
                "mapping": {"description": "Map fields", "fields": ["source_path", "target_path", "transformation_logic"]},
                "conditional": {"description": "Conditional field processing", "fields": ["condition_expression", "source_path", "target_path"]},
                "custom": {"description": "Custom logic", "fields": ["transformation_logic", "source_path", "target_path"]},
                "set_variable": {"description": "Set variable to value", "fields": ["variable_name", "value"]},
                "loop": {"description": "Define loop over items", "fields": ["source", "variable_name", "index_variable"]},
                "append_segment": {"description": "Append HL7 segment", "fields": ["segment_name", "content"]},
                "append_line": {"description": "Append line to output", "fields": ["content"]},
                "comment": {"description": "No-op with note", "fields": ["text"]}
            },

            "common_hl7_fields": {
                "MSH.3": "Sending Application",
                "MSH.4": "Sending Facility",
                "MSH.5": "Receiving Application",
                "MSH.6": "Receiving Facility",
                "PID.3": "Patient ID",
                "PID.5.1": "Patient Last Name",
                "PID.5.2": "Patient First Name",
                "PID.7": "Patient DOB",
                "PID.8": "Patient Gender",
                "PV1.2": "Patient Class",
                "PV1.3": "Assigned Patient Location"
            }
        }

    async def _call_openrouter_api(self, system_prompt: str, user_message: str) -> Dict[str, Any]:
        """Call OpenRouter API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://meddataflow.com",
            "X-Title": "meddataflow Workflow Generator"
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.1,  # Low temperature for consistent, structured output
            "max_tokens": 4000
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            )

            if response.status_code != 200:
                raise Exception(f"OpenRouter API error: {response.status_code} - {response.text}")

            return response.json()

    def _parse_ai_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse and validate AI response"""
        try:
            # Log the entire response for debugging
            logger.error(f"Full AI response data: {json.dumps(response_data, indent=2)}")

            content = response_data["choices"][0]["message"]["content"]
            logger.error(f"Raw AI response content: '{content}'")
            logger.error(f"Content type: {type(content)}, Length: {len(content) if content else 0}")

            # Try to extract JSON from the response
            if content and "```json" in content:
                # Extract JSON from code block
                start = content.find("```json") + 7
                end = content.find("```", start)
                json_content = content[start:end].strip()
                logger.error(f"Extracted JSON from code block: '{json_content}'")
            elif content and content.strip().startswith('{'):
                # Assume the entire content is JSON if it starts with {
                json_content = content.strip()
                logger.error(f"Using entire content as JSON: '{json_content}'")
            elif content:
                # Content doesn't look like JSON - this is the issue we're seeing
                logger.error(f"Content doesn't appear to be JSON. Content starts with: '{content[:200]}...'")
                raise ValueError(f"AI returned explanatory text instead of JSON. Content: {content[:200]}...")
            else:
                logger.error("Content is None or empty")
                raise ValueError("AI response content is None or empty")

            if not json_content:
                logger.error("JSON content is empty after extraction")
                raise ValueError("Empty JSON content extracted from AI response")

            workflow_config = json.loads(json_content)

            # Validate required fields
            required_fields = ["name", "description", "activities"]
            for field in required_fields:
                if field not in workflow_config:
                    raise ValueError(f"Missing required field: {field}")

            # Validate activities
            if not isinstance(workflow_config["activities"], list):
                raise ValueError("Activities must be a list")

            for i, activity in enumerate(workflow_config["activities"]):
                if "activity_type" not in activity:
                    raise ValueError(f"Activity {i} missing activity_type")
                if "name" not in activity:
                    raise ValueError(f"Activity {i} missing name")
                if "config" not in activity:
                    workflow_config["activities"][i]["config"] = {}
                if "transformers" not in activity:
                    workflow_config["activities"][i]["transformers"] = []

            return workflow_config

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in AI response: {e}")
        except KeyError as e:
            raise ValueError(f"Unexpected API response format: {e}")
        except Exception as e:
            raise ValueError(f"Failed to parse AI response: {e}")


# Global AI service instance
ai_workflow_service = AIWorkflowService()
