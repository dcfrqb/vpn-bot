"""Remnawave panel adapter. Owner stream: B (Panel core).

Foundation leaves the existing client at app.remnawave.client; the port
``RemnaGateway`` (app.services.ports) is implemented over it by
app.services.shims.LegacyRemnaGateway. Stream B moves/rewrites the httpx
client here with DTOs from impl/remnawave_3.4.3_openapi.json.
"""
