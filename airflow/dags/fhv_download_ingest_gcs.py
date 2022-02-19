#from aifc import Aifc_read
import os
import logging
import pyarrow.csv as pv
import pyarrow.parquet as pq
from datetime import datetime
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

from google.cloud import storage
from airflow.providers.google.cloud.operators.bigquery import BigQueryCreateExternalTableOperator

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "dataengineering-bizzy")
BUCKET = os.environ.get("GCP_GCS_BUCKET", "dtc_data_lake_dataengineering-bizzy")
BIGQUERY_DATASET = os.environ.get("FHV_DATASET", 'fhv_trips_data')

Airflow_Home = os.environ.get("AIRFLOW_HOME", "/opt/airflow/")
url_prefix = 'https://nyc-tlc.s3.amazonaws.com/trip+data'
url_template = url_prefix + '/fhv_tripdata_{{ execution_date.strftime(\'%Y-%m\') }}.csv'
output_file_template= Airflow_Home +'/fhv_output_{{ execution_date.strftime(\'%Y-%m\') }}.csv'
parquet_file = 'fhv_output_{{ execution_date.strftime(\'%Y-%m\') }}.parquet'



def format_2_parquet(src_file):
    if not src_file.endswith('.csv'):
        logging.error("Can only accept source files in CSV format, for the moment")
        return
    table = pv.read_csv(src_file)
    pq.write_table(table, src_file.replace('.csv', '.parquet'))

# NOTE: takes 20 mins, at an upload speed of 800kbps. Faster if your internet has a better upload speed
def upload_to_gcs(bucket, object_name, local_file):
    """
    Ref: https://cloud.google.com/storage/docs/uploading-objects#storage-upload-object-python
    :param bucket: GCS bucket name
    :param object_name: target path & file-name
    :param local_file: source path & file-name
    :return:
    """
    # WORKAROUND to prevent timeout for files > 6 MB on 800 kbps upload speed.
    # (Ref: https://github.com/googleapis/python-storage/issues/74)
    storage.blob._MAX_MULTIPART_SIZE = 5 * 1024 * 1024  # 5 MB
    storage.blob._DEFAULT_CHUNKSIZE = 5 * 1024 * 1024  # 5 MB
    # End of Workaround

    client = storage.Client()
    bucket = client.bucket(bucket)

    blob = bucket.blob(object_name)
    blob.upload_from_filename(local_file)



load_workflow= DAG(
    "fhv_ingest_2_gcs_dag", 
    catchup=True,
    schedule_interval="0 6 2 * *",
    start_date= datetime(2019, 1, 1),
    end_date = datetime(2020,12,31),
    max_active_runs= 3

)

with load_workflow:

    curl_fhv_job= BashOperator(
        task_id='fhv_file_url',
        bash_command=f'curl -sSLf {url_template} > {output_file_template}'
    )
    
    format_csv_to_parquet_task = PythonOperator(
        task_id="format_csv_2_parquet_task",
        python_callable=format_2_parquet,
        op_kwargs={
            "src_file": f"{output_file_template}",
        },
    )

    upload_to_gcs_job = PythonOperator(
        task_id="upload_to_gcs_job",
        python_callable=upload_to_gcs,
        op_kwargs={
            "bucket": BUCKET,
            "object_name": f"raw/fhv/{parquet_file}",
            "local_file": f"{Airflow_Home}/{parquet_file}",
        },
        
    )

    cleanup_job= BashOperator(
        task_id='cleanup_csv',
        bash_command=f'rm {output_file_template}'
    )




curl_fhv_job >> format_csv_to_parquet_task >> upload_to_gcs_job