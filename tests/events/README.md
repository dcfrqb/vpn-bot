# tests/events - stream C (Panel events)

Scope: POST /webhook/remnawave, reminders, maintenance, grace.

- Use fakes from `tests/fakes` (FakeRemnaGateway, FakePaymentGateway, FakeStarsGateway,
  RecordingNotifier, FakeRedis, FakeClock, bot.make_bot) instead of MagicMock where possible.
- End-to-end bot behaviour goes to `tests/flows/` (real Dispatcher, fixture `flow`).
- Build services through `app.container.build_container(bot, <port>=<fake>)`.
- When your handler takes over a packed callback, remove it from
  `PENDING_PACKED` in `tests/flows/test_callback_matrix.py`.
- Tests run in CI with `pytest tests/ -m "not integration"`; real-Postgres tests are
  marked `integration` and read `HOTFIX_PG_URL`.
