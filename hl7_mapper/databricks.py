"""
This file keeps the code required to access and work with databricks api 
https://databricks-sdk-py.readthedocs.io/en/latest/getting-started.html
https://delta-io.github.io/delta-rs/python/usage.html#writing-delta-tables

"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout as timeout
import json
def read_fron_databricks():
    with open(".vscode/secrets.json", 'r') as f:
        result = json.loads(f.read())
    print(result)
    w = WorkspaceClient(host=result['host'], token=result['token'])
    srcs = w.data_sources.list()
    response = w.statement_execution.execute_statement('select * from athena.patient_index',result['workspace_id'], catalog='main', wait_timeout='30s', on_wait_timeout=timeout.CANCEL)
    print(srcs)