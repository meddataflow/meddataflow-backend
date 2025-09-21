import boto3
import pandas as pd
import json

class AWSConnector:
    """
    This class includes the functionalily to connect and use AWS services
    For the calls to aws to work outside databricks environment, there either 
    needs to be a long term API key or the user has to authenticate using the 
    `aws sso login` of the aws cli. 
    see https://docs.aws.amazon.com/sdkref/latest/guide/access-iam-users.html
    to create long term access keys
    """
    def __init__(self, profile_name='default'):
        self.session = boto3.Session(profile_name=profile_name)

    def read_HL7_batch_from_S3(self, bucket:str, key:str)->pd.DataFrame:
        """
            given a bucket and a path to file, this function will read a batch
            of HL7 messages into a pandas dataframe.
            the dataframe has a single column, header is 'message'
            it is expected that the messages in the batch file are 
            concatenated with no separators other than \r or \n.
        """
        client = boto3.client('s3')
        object = client.get_object(Bucket=bucket, Key=key)
        data = object['Body'].read().decode('utf-8')
        data = ['MSH' + d for d in data.split('MSH')[1:]]
        df = pd.DataFrame([data], columns=['message'])
        return df

    def read_secret(self, secrert_id):
        client = self.session.client('secretsmanager')
        secret = client.get_secret_value(SecretId=secrert_id)
        return json.loads(secret['SecretString'])