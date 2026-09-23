"""Domain layer (release 3.0): pure data and rules, no I/O, no aiogram.

- plans.py   - plan catalog and prices (the ONLY price source).
- models.py  - DTOs shared by services, bot and api (frozen seam).
- texts/     - user-facing text helpers and per-area text modules.

Nothing in this package may import aiogram, SQLAlchemy sessions, httpx or Redis.
"""
