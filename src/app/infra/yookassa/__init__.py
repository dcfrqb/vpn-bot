"""YooKassa adapter. Owner stream: A (Money).

Foundation leaves the SDK calls in app.services.payments.yookassa; the port
``PaymentGateway`` is implemented over it by app.services.shims.
Stream A adds an async httpx client here.
"""
