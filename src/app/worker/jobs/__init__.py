"""Job modules. Each exposes ``async def run(ctx: JobContext) -> None``.

Foundation: jobs/legacy.py wraps the 2.x tasks as-is.
Streams add one module per job (recovery, autopay, device_cleanup,
obhod_lifecycle, grace, panel_health, reminders, panel_sync) and register it
in app.worker.scheduler.build_jobs() via an orchestrator commit.
"""
