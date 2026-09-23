"""Remnawave panel adapter. Owner stream: B (Panel core).

- client.py  - own httpx client (transport; 2.x surface kept until cutover),
               old path app.remnawave.client is an alias of this module;
- util.py    - pure helpers (ids, dates, payloads, errors);
- legacy.py  - 2.x-only client methods (mixin), removed at cutover;
- dto.py     - DTOs from the 3.4.3 contract (impl/remnawave_3.4.3_openapi.json);
- gateway.py - HttpRemnaGateway, the RemnaGateway port used by 3.0 code.
"""
