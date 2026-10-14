from celery import shared_task

from .jobs import execute_export_job, purge_old_export_jobs


@shared_task(name="reports.run_export_job", soft_time_limit=1800, time_limit=1900)
def run_export_job(job_id):
    execute_export_job(job_id)


@shared_task(name="reports.purge_old_exports")
def purge_old_exports():
    return purge_old_export_jobs()
