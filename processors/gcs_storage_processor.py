"""
Google Cloud Storage activity processor for workflow execution
"""
import uuid
import json
from datetime import datetime
from typing import Dict, Any
import logging

from models.workflow_models import WorkflowContext, ActivityResult, ActivityStatus

logger = logging.getLogger(__name__)

async def process_gcs_storage_activity(
    activity: Dict[str, Any],
    context: WorkflowContext
) -> ActivityResult:
    """
    Process Google Cloud Storage activity - Store files in GCS bucket
    """
    config = activity.get("config", {})

    # Handle both direct bucket name and nested bucket config
    bucket_config = config.get("bucket", {})
    if isinstance(bucket_config, dict):
        bucket = bucket_config.get("name", "hl7-processed-files")
        key_pattern = bucket_config.get("key_prefix", "{tenant_id}/{date}/") + bucket_config.get("file_pattern", "{filename}")
    else:
        bucket = bucket_config if bucket_config else "hl7-processed-files"
        key_pattern = config.get("key_pattern", "{tenant_id}/{date}/{filename}")

    # GCS specific settings
    gcs_config = config.get("gcs", {})
    project_id = gcs_config.get("project_id", "")
    region = gcs_config.get("region", "us-central1")
    storage_class = gcs_config.get("storage_class", "STANDARD")

    # Authentication settings
    auth_config = config.get("authentication", {})
    service_account_key = auth_config.get("service_account_key", "")
    use_default_credentials = auth_config.get("use_default_credentials", True)

    # Storage options
    storage_config = config.get("storage", {})
    content_type = storage_config.get("content_type", "auto")
    encryption = storage_config.get("encryption", False)
    lifecycle_enabled = storage_config.get("lifecycle_enabled", False)
    public_read = storage_config.get("public_read", False)

    # Get content to store - try multiple sources
    csv_content = context.variables.get("csv_content")
    csv_filename = context.variables.get("csv_filename", f"data_{uuid.uuid4().hex}.csv")

    # If no CSV content, try to create JSON content from available variables
    if not csv_content:
        # Create JSON content from available variables (excluding system variables)
        system_vars = {"source", "metadata", "trigger_type", "message"}
        data_vars = {k: v for k, v in context.variables.items()
                    if not k.startswith("http_") and not k.startswith("file_")
                    and not k.startswith("email_") and not k.startswith("db_")
                    and not k.startswith("gcs_") and k not in system_vars}

        if data_vars:
            # Custom JSON encoder to handle UUID and other non-serializable objects
            def json_serializer(obj):
                if hasattr(obj, 'hex'):  # UUID objects
                    return str(obj)
                elif isinstance(obj, datetime):
                    return obj.isoformat()
                else:
                    return str(obj)

            csv_content = json.dumps(data_vars, indent=2, default=json_serializer)
            csv_filename = f"workflow_data_{uuid.uuid4().hex[:8]}.json"
        else:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="No content available to store (no csv_content or extractable data)"
            )

    # Generate GCS object path
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    timestamp_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    # Handle both single and double curly brace patterns
    object_name = key_pattern
    substitutions = {
        "tenant_id": context.tenant_id,
        "date": date_str,
        "filename": csv_filename,
        "message_id": context.variables.get("MESSAGE_CONTROL_ID", "unknown"),
        "timestamp": timestamp_str,
        **context.variables  # Include all variables for substitution
    }

    # Replace both {{var}} and {var} patterns
    for var_name, var_value in substitutions.items():
        double_placeholder = f"{{{{{var_name}}}}}"
        single_placeholder = f"{{{var_name}}}"
        object_name = object_name.replace(double_placeholder, str(var_value))
        object_name = object_name.replace(single_placeholder, str(var_value))

    try:
        # Try to use Google Cloud Storage client
        from google.cloud import storage

        # Initialize client
        if use_default_credentials:
            client = storage.Client(project=project_id)
        elif service_account_key:
            from google.oauth2 import service_account

            # Parse service account key (could be JSON string or file path)
            if service_account_key.startswith('{'):
                # JSON string
                credentials_info = json.loads(service_account_key)
                credentials = service_account.Credentials.from_service_account_info(credentials_info)
            else:
                # File path
                credentials = service_account.Credentials.from_service_account_file(service_account_key)

            client = storage.Client(project=project_id, credentials=credentials)
        else:
            return ActivityResult(
                status=ActivityStatus.FAILED,
                error_message="GCS authentication not configured. Set service_account_key or use_default_credentials."
            )

        # Get bucket
        gcs_bucket = client.bucket(bucket)

        # Create blob
        blob = gcs_bucket.blob(object_name)

        # Set content type
        if content_type == "auto":
            if csv_filename.endswith('.json'):
                blob.content_type = "application/json"
            elif csv_filename.endswith('.csv'):
                blob.content_type = "text/csv"
            elif csv_filename.endswith('.xml'):
                blob.content_type = "application/xml"
            else:
                blob.content_type = "text/plain"
        else:
            blob.content_type = content_type

        # Set metadata
        metadata = {
            "workflow_id": str(context.workflow_id),
            "execution_id": str(context.execution_id),
            "tenant_id": str(context.tenant_id),
            "message_type": context.variables.get("MESSAGE_TYPE", ""),
            "processed_at": datetime.utcnow().isoformat(),
            "source": "meddataflow"
        }

        # Add custom metadata from config
        custom_metadata = config.get("metadata", [])
        for meta in custom_metadata:
            if meta.get("key") and meta.get("value"):
                metadata[meta["key"]] = meta["value"]

        blob.metadata = metadata

        # Upload content
        blob.upload_from_string(
            csv_content,
            content_type=blob.content_type
        )

        # Set public access if requested
        if public_read:
            blob.make_public()

        # Get public URL if public, otherwise signed URL
        if public_read:
            gcs_url = blob.public_url
        else:
            gcs_url = f"gs://{bucket}/{object_name}"

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "gcs_bucket": bucket,
                "gcs_object": object_name,
                "gcs_url": gcs_url,
                "project_id": project_id,
                "region": region,
                "file_size": len(csv_content.encode()),
                "content_type": blob.content_type,
                "upload_timestamp": datetime.utcnow().isoformat(),
                "public_url": blob.public_url if public_read else None
            },
            variables={
                "gcs_bucket": bucket,
                "gcs_object": object_name,
                "gcs_url": gcs_url,
                "gcs_public_url": blob.public_url if public_read else None
            }
        )

    except ImportError:
        logger.error("Google Cloud Storage library not installed. Install with: pip install google-cloud-storage")
        return ActivityResult(
            status=ActivityStatus.FAILED,
            error_message="Google Cloud Storage library not installed. Contact administrator to install google-cloud-storage package."
        )
    except Exception as e:
        logger.error(f"Failed to upload to GCS: {e}")

        # Fallback: Store locally
        import os
        local_dir = f"/tmp/hl7_files/{context.tenant_id}/{date_str}"
        os.makedirs(local_dir, exist_ok=True)

        local_path = os.path.join(local_dir, csv_filename)
        with open(local_path, 'w') as f:
            f.write(csv_content)

        return ActivityResult(
            status=ActivityStatus.COMPLETED,
            output_data={
                "storage_type": "local",
                "local_path": local_path,
                "file_size": len(csv_content.encode()),
                "note": f"GCS upload failed, stored locally: {e}"
            },
            variables={
                "storage_type": "local",
                "local_path": local_path
            }
        )