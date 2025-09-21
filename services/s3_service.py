import boto3
import logging
from typing import Optional, Dict, Any
from botocore.exceptions import ClientError, NoCredentialsError
from datetime import datetime
import uuid
import os

logger = logging.getLogger(__name__)

class S3Service:
    def __init__(self):
        self.aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID', 'test')
        self.aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY', 'test')
        self.aws_region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
        self.bucket_name = os.getenv('S3_BUCKET_NAME', 'hl7-test-bucket')
        self.endpoint_url = os.getenv('AWS_ENDPOINT_URL')  # For LocalStack

        # For LocalStack testing, use default test credentials
        if not self.endpoint_url and not all([self.aws_access_key_id, self.aws_secret_access_key]):
            logger.warning("S3 configuration incomplete. Some features may not work.")
            self.s3_client = None
        else:
            try:
                client_config = {
                    'aws_access_key_id': self.aws_access_key_id,
                    'aws_secret_access_key': self.aws_secret_access_key,
                    'region_name': self.aws_region
                }

                # Add endpoint URL for LocalStack
                if self.endpoint_url:
                    client_config['endpoint_url'] = self.endpoint_url

                self.s3_client = boto3.client('s3', **client_config)

                # Try to create bucket if it doesn't exist (for LocalStack)
                try:
                    self.s3_client.head_bucket(Bucket=self.bucket_name)
                    logger.info(f"Successfully connected to S3 bucket: {self.bucket_name}")
                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code == '404':  # Bucket doesn't exist
                        try:
                            self.s3_client.create_bucket(Bucket=self.bucket_name)
                            logger.info(f"Created S3 bucket: {self.bucket_name}")
                        except Exception as create_error:
                            logger.warning(f"Could not create bucket {self.bucket_name}: {create_error}")
                    else:
                        raise e

            except Exception as e:
                logger.error(f"Failed to connect to S3: {e}")
                self.s3_client = None
    
    def is_available(self) -> bool:
        """Check if S3 service is available and properly configured"""
        return self.s3_client is not None
    
    async def upload_file(
        self, 
        content: str, 
        filename: str, 
        tenant_id: str,
        workflow_id: str = None,
        execution_id: str = None,
        content_type: str = 'text/plain',
        metadata: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """
        Upload file content to S3
        
        Args:
            content: File content as string
            filename: Desired filename
            tenant_id: Tenant ID for organizing files
            workflow_id: Optional workflow ID
            execution_id: Optional execution ID
            content_type: MIME type of the content
            metadata: Additional metadata to store with the file
            
        Returns:
            Dict with upload results including URL and metadata
        """
        if not self.is_available():
            raise Exception("S3 service is not available. Please check configuration.")
        
        try:
            # Generate a unique file key
            timestamp = datetime.utcnow().strftime("%Y/%m/%d")
            file_id = str(uuid.uuid4())
            
            # Organize files by tenant, date, and optionally workflow
            if workflow_id:
                s3_key = f"tenants/{tenant_id}/workflows/{workflow_id}/{timestamp}/{file_id}_{filename}"
            else:
                s3_key = f"tenants/{tenant_id}/files/{timestamp}/{file_id}_{filename}"
            
            # Prepare metadata
            s3_metadata = {
                'tenant_id': tenant_id,
                'uploaded_at': datetime.utcnow().isoformat(),
                'file_id': file_id
            }
            
            if workflow_id:
                s3_metadata['workflow_id'] = workflow_id
            
            if execution_id:
                s3_metadata['execution_id'] = execution_id
            
            if metadata:
                s3_metadata.update(metadata)
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=content.encode('utf-8'),
                ContentType=content_type,
                Metadata=s3_metadata,
                ServerSideEncryption='AES256'  # Encrypt at rest
            )
            
            # Generate presigned URL for access (valid for 7 days)
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': s3_key},
                ExpiresIn=604800  # 7 days
            )
            
            logger.info(f"Successfully uploaded file {filename} to S3: {s3_key}")
            
            return {
                'success': True,
                'file_id': file_id,
                's3_key': s3_key,
                'url': url,
                'bucket': self.bucket_name,
                'filename': filename,
                'content_type': content_type,
                'size_bytes': len(content.encode('utf-8')),
                'metadata': s3_metadata,
                'uploaded_at': datetime.utcnow().isoformat()
            }
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(f"S3 ClientError: {error_code} - {error_message}")
            
            return {
                'success': False,
                'error': f"S3 Error: {error_code} - {error_message}",
                'error_code': error_code
            }
        
        except Exception as e:
            logger.error(f"Unexpected error uploading to S3: {e}")
            return {
                'success': False,
                'error': f"Upload failed: {str(e)}"
            }
    
    async def upload_csv(
        self,
        csv_content: str,
        filename: str,
        tenant_id: str,
        workflow_id: str = None,
        execution_id: str = None,
        metadata: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """Upload CSV content to S3"""
        return await self.upload_file(
            content=csv_content,
            filename=filename if filename.endswith('.csv') else f"{filename}.csv",
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            content_type='text/csv',
            metadata=metadata
        )
    
    async def upload_json(
        self,
        json_content: str,
        filename: str,
        tenant_id: str,
        workflow_id: str = None,
        execution_id: str = None,
        metadata: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """Upload JSON content to S3"""
        return await self.upload_file(
            content=json_content,
            filename=filename if filename.endswith('.json') else f"{filename}.json",
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            content_type='application/json',
            metadata=metadata
        )

    async def upload_content(
        self,
        bucket: str,
        key: str,
        content: str,
        content_type: str = 'text/plain',
        metadata: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """Upload content to S3 with specific bucket and key - used by processors"""
        if not self.is_available():
            raise Exception("S3 service is not available. Please check configuration.")

        try:
            # Prepare metadata
            s3_metadata = {
                'uploaded_at': datetime.utcnow().isoformat(),
            }

            if metadata:
                s3_metadata.update(metadata)

            # Upload to S3
            self.s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=content.encode('utf-8'),
                ContentType=content_type,
                Metadata=s3_metadata,
                ServerSideEncryption='AES256'  # Encrypt at rest
            )

            logger.info(f"Successfully uploaded content to S3: s3://{bucket}/{key}")

            return {
                'success': True,
                'bucket': bucket,
                'key': key,
                's3_url': f"s3://{bucket}/{key}",
                'size_bytes': len(content.encode('utf-8')),
                'metadata': s3_metadata,
                'uploaded_at': datetime.utcnow().isoformat()
            }

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(f"S3 ClientError: {error_code} - {error_message}")
            raise Exception(f"S3 Error: {error_code} - {error_message}")

        except Exception as e:
            logger.error(f"Unexpected error uploading to S3: {e}")
            raise Exception(f"Upload failed: {str(e)}")
    
    async def upload_xml(
        self,
        xml_content: str,
        filename: str,
        tenant_id: str,
        workflow_id: str = None,
        execution_id: str = None,
        metadata: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """Upload XML content to S3"""
        return await self.upload_file(
            content=xml_content,
            filename=filename if filename.endswith('.xml') else f"{filename}.xml",
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            content_type='application/xml',
            metadata=metadata
        )
    
    async def get_file(self, s3_key: str) -> Optional[str]:
        """Retrieve file content from S3"""
        if not self.is_available():
            raise Exception("S3 service is not available.")
        
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            content = response['Body'].read().decode('utf-8')
            return content
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return None
            raise e
    
    async def delete_file(self, s3_key: str) -> bool:
        """Delete file from S3"""
        if not self.is_available():
            raise Exception("S3 service is not available.")
        
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            logger.info(f"Successfully deleted file from S3: {s3_key}")
            return True
        except ClientError as e:
            logger.error(f"Failed to delete file from S3: {e}")
            return False
    
    async def list_tenant_files(
        self, 
        tenant_id: str, 
        prefix: str = "", 
        max_files: int = 100
    ) -> Dict[str, Any]:
        """List files for a tenant"""
        if not self.is_available():
            raise Exception("S3 service is not available.")
        
        try:
            s3_prefix = f"tenants/{tenant_id}/{prefix}" if prefix else f"tenants/{tenant_id}/"
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=s3_prefix,
                MaxKeys=max_files
            )
            
            files = []
            if 'Contents' in response:
                for obj in response['Contents']:
                    # Get metadata
                    try:
                        metadata_response = self.s3_client.head_object(
                            Bucket=self.bucket_name, 
                            Key=obj['Key']
                        )
                        metadata = metadata_response.get('Metadata', {})
                    except:
                        metadata = {}
                    
                    files.append({
                        'key': obj['Key'],
                        'filename': obj['Key'].split('/')[-1],
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat(),
                        'metadata': metadata,
                        'url': self.s3_client.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': self.bucket_name, 'Key': obj['Key']},
                            ExpiresIn=3600  # 1 hour
                        )
                    })
            
            return {
                'success': True,
                'files': files,
                'total_count': len(files),
                'is_truncated': response.get('IsTruncated', False)
            }
            
        except ClientError as e:
            logger.error(f"Failed to list S3 files: {e}")
            return {
                'success': False,
                'error': str(e),
                'files': []
            }

# Global S3 service instance
s3_service = S3Service()