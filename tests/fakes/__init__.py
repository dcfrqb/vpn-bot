"""Фейки внешних сервисов для тестов (без сети).

remnawave.FakeRemna        - поверхность RemnaClient 2.x (in-memory панель)
remnawave.FakeRemnaGateway - порт RemnaGateway 3.0 поверх FakeRemna
redis.FakeRedis            - async Redis
payments.FakePaymentGateway, stars.FakeStarsGateway
notifier.RecordingNotifier - порт Notifier
clock.FakeClock            - управляемое время
bot                        - RecordingSession / make_bot / апдейты для Dispatcher
"""
