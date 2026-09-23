"""Telegram layer of release 3.0 (aiogram 3.31).

- callbacks.py        - ALL CallbackData classes (frozen seam).
- legacy_aliases.py   - rewrites 2.x callback strings to packed callbacks.
- middlewares/        - di, errors, maintenance.
- routers/            - ordered ROUTERS; one module per stream area.
- views/              - keyboard/render helpers shared by routers.

Handlers are thin: parse -> call a port (injected by DI) -> render a view.
No business rules, no SQL, no panel/YooKassa calls in this package.
"""
