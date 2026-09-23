"""Autopay job (release 3.0, stream A). OFF twice: TASK_AUTOPAY_ENABLED and AUTOPAY_ENABLED.

``run(ctx)``: notices 3 days before the end, charges of the saved card 1 day
before (see app.services.autopay). Register with
``Job("autopay", autopay.run, 3600, flag="AUTOPAY")`` (requests/A.md).
"""
from __future__ import annotations

from app.services.money import money


async def run(ctx) -> dict:
    return await money(ctx.container).autopay.run_once()
