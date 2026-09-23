# Architecture of release 3.0

Read this before touching the code in a 3.0 stream. The authoritative plan is
`Servers/docs/РЕВЬЮ_БОТА_2026-09-23/ПЛАН_3.0_ТЕХ.md`; this file describes what
Foundation actually built and how to extend it.

## Layers

```
app/
  domain/        pure rules and DTOs, no I/O, no aiogram
    plans.py     plan catalog; the ONLY source of prices (core/plans.py is an alias)
    models.py    DTOs: Quote, Entitlement, SubscriptionState, DeviceInfo, PanelUser,
                 PromoReward, PaymentIntent, AdminTopic, SubKind, Payment* enums
    texts/       helpers h, plural_ru, n_plural, fmt_date_msk, fmt_rub, fmt_gb
                 + one text module per area (common, menu, checkout, connect, devices,
                 promo, admin, notify)
  infra/         adapters to the outside world
    redis/       flags (set_once, counters, once-markers), locks (user lock,
                 LeaderLock), cache (JSON)
    remnawave/   client.py (own httpx, 3.4.3), dto.py, gateway.py (stream B);
                 app/remnawave/client.py is an alias of client.py
    yookassa/    stream A adds the async client here
    telegram_stars.py
  services/
    ports.py     Protocols every consumer depends on
    notifications.py  TelegramNotifier (admin topics + DM fallback)
    ...          3.0 services (checkout, fulfillment, provisioning, status, promo, ...)
                 and the 2.x services that are still used (users, obhod_service, ...)
  bot/
    callbacks.py       every CallbackData class
    legacy_aliases.py  2.x callback strings -> packed callbacks
    dispatcher.py      build_dispatcher (no I/O)
    middlewares/       di, errors, maintenance
    routers/           one module per area, ordered by routers/__init__.py
    views/             btn, url_btn, kb, render
  worker/
    scheduler.py       one loop, leader lock, JOBS registry (build_jobs)
    jobs/              one module per job (legacy.py: 2.x expiry notifier + reconciler)
  api/
    app.py             FastAPI assembly; api/main.py and api/server.py are shims
    routes/            yookassa, remnawave (panel webhook), health
    internal_site.py   /internal/site/* for the site, contract frozen
  container.py         composition root (the only place that picks implementations)
```

Dependency direction: `bot`, `api`, `worker` -> `services.ports` -> `domain`.
Implementations (`services/*`, `infra/*`) are wired only in `container.py`.
Nothing in `domain` or `services` imports aiogram or FastAPI.

## Ports (`app/services/ports.py`)

| Port | Owner | Implementation (wired in `container.build_container`) |
|---|---|---|
| RemnaGateway | B | `infra.remnawave.gateway.HttpRemnaGateway` (one per process) |
| PaymentGateway | A | `infra.yookassa.gateway.YooKassaGateway` |
| StarsGateway | A | `infra.telegram_stars.TelegramStarsGateway` |
| ProvisioningService | B | `services.provisioning.PanelProvisioningService` |
| StatusService | B | `services.status.PanelStatusService` |
| DevicesService | B | `services.devices.PanelDevicesService` |
| CheckoutService | A | `services.checkout.ContainerCheckout` -> `money(container).checkout` |
| PromoService | E | `services.promo.PromoEngine` |
| Notifier | Foundation | `notifications.TelegramNotifier` |
| MaintenanceGuard | C | `services.maintenance.RedisMaintenanceGuard` |

Services that depend on an overridden port are built over the override
(`build_container(bot, remna=Fake...)` gives provisioning/status/devices over the fake).
`services/shims.py` was deleted at the cutover.

Handlers get ports from DI by name: `container, remna, payments, stars,
provisioning, status_service, devices, checkout, promo, notifier, maintenance`.
Jobs get `ctx.container`; API routes call `app.container.get_container()`.

To ship a real implementation: add the class in your area, change ONE line in
`container.build_container`, keep the signature. Tests build containers with
fakes: `build_container(bot, remna=FakeRemnaGateway(), notifier=RecordingNotifier())`.

## Panel (stream B)

- Panel accounts are created ONLY by `ProvisioningService.grant` (alias
  `provision`). `/start`, status, devices and the site only look up
  (`services.accounts.PanelAccounts.find_main`).
- Payments: `grant(tg, Entitlement(plan_code, source=PAYMENT, payment_id=...),
  trace_id=..., months=N)` (calendar months from max(now, current expiry)).
  Promo/trial/admin: `Entitlement(days=N)` or `until=` or `is_lifetime=True`.
  Optional kwargs: `enable_if_disabled` (admin approved a payment of a
  DISABLED user), `clear_grace` (default True). Idempotent per `payment_id`,
  else per `trace_id`. `GrantRefused` (reason `disabled`, `bad_plan`,
  `bad_entitlement`) = nothing written; `ProvisioningError` = retry.
- Credits for existing accounts: `add_days`, `add_traffic`, `add_devices`
  (never lowering, idempotent per trace_id). Refund: `revoke`.
- Squads: only the bot's tariff squads (and the grace squad) are swapped;
  manual squads (`*-m`, `*-friend`, `arcadia`) are never written; the HWID
  limit is never lowered. Squad names are cached 10 min in the gateway.
- Jobs (default off): `panel_sync` (DB <- panel, never writes the panel),
  `device_cleanup` (dry run by default), `obhod_lifecycle`.

## Former frozen files

The streams are merged; the seams (`bot/callbacks.py`, `services/ports.py`,
`domain/models.py`, `container.py`, `bot/routers/__init__.py`,
`worker/scheduler.py`, `api/app.py`) are ordinary files now. The 2.x UI
stack (`ui/`, `navigation/`, `keyboards/`, `legacy/`, `payments/ui/`, the 2.x
routers except `routers/site_login.py`) was deleted at the cutover.

## Router order

```
site_login                     2.x, first: /start login_* and sitelogin: never reach others
r3_promo_deeplink  (E)         /start <code>, /start g_<code>
r3_trial_promo     (E)         /trial /promo /solokhin /sun718 /friend, promo buttons
r3_start           (D)         /start /help /devices /myid /profile
r3_menu, r3_checkout (A), r3_connect, r3_devices, r3_support, r3_refund (A)
r3_admin_payments (A), r3_admin_home, r3_admin_users, r3_admin_grants, r3_admin_ops,
r3_admin_promo, r3_admin_broadcast, r3_admin_obhod (E), r3_admin_panel (C)
r3_fallback        (D)         any callback nobody took: answer + main menu (last)
tg_errors_global               Telegram API errors
```

New routers get `ErrorsMiddleware`: the user sees a generic text, the admin
ERRORS topic gets the details (dedup 10 min), Telegram API errors go to
`tg_errors_global` as before.

Outer middlewares: `dp.update` DI; `dp.message` and `dp.callback_query`
maintenance (no-op unless `maintenance:state` is set in Redis; admins pass);
`dp.callback_query` legacy aliases.

## Legacy aliases

`bot/legacy_aliases.py` maps every 2.x callback string to a packed callback
(ordered table `ALIASES`). For each old button press it increments
`legacy_hits:<alias key>` in Redis, builds the packed callback and checks
whether any handler of the 3.0 routers accepts it. If yes, the event continues
with the new data (a `model_copy`, still bound to the Bot); if no, the original
event continues (only site_login and `r3_fallback` are left for it).
`tests/flows/test_callback_matrix.py` lists every string the 2.1.1 code
produced and proves each reaches exactly one specific 3.0 handler.

`pay_yookassa_<plan>_<months>_<amount>` becomes `Period(plan, months)`; the
amount is ignored. `bc:unsub` / `bc:close` are already valid `Bc` callbacks.
`sitelogin:*` is never rewritten nor counted. Hit counts for an admin screen:
`await legacy_aliases.alias_hit_counts()`. Keep aliases at least 3 months
after 3.0.

## Flags

All in `app/config.py`, one section per stream, every new behaviour default
OFF (except new menu/checkout screens). Background jobs have
`TASK_<NAME>_ENABLED` (typo = OFF) under the master `BACKGROUND_TASKS_ENABLED`.
3.0 flags: `AUTOPAY_ENABLED, STARS_ENABLED, STARS_RATE, GIFTS_ENABLED,
REFUND_24H_ENABLED, PROMO_CODES_ENABLED, DEVICES_UNLINK_ENABLED,
PANEL_WEBHOOK_SECRET, MAINTENANCE_AUTO_ENABLED, GRACE_ENABLED, GRACE_SQUAD,
GRACE_DAYS, GRACE_DAILY_GB, DEVICE_CLEANUP_DAYS, DEVICE_CLEANUP_DRY_RUN,
RECONCILER_INTERVAL_S, ADMIN_CHAT_ID, ADMIN_TOPIC_*, CONNECT_ARTICLE_URL,
PRIVACY_URL, SUPPORT_HANDLE, TASK_{DEVICE_CLEANUP, OBHOD_LIFECYCLE, AUTOPAY,
GRACE, PANEL_HEALTH, REMINDERS, PANEL_SYNC}_ENABLED`. Every setting is listed in
`.env.example`; a test checks that.

## How to add

**A callback.** Use an existing class from `bot/callbacks.py` if its fields fit
(`Nav(s, p)`, `Adm(s, a, arg)` carry many screens). A new class or field is an
orchestrator commit: short prefix, no ":" in values, no money, worst case
added to `tests/test_r3_callbacks.py`, sample added to `PACKED_SAMPLES` in
`tests/flows/test_callback_matrix.py`. When your handler lands, delete its
sample from `PENDING_PACKED` (the matrix test fails if you forget).

**A router handler.** Put it in your router module under `bot/routers/`,
filter on a CallbackData class, take ports from DI, render with
`bot/views.render`. No SQL, no panel or YooKassa calls, no prices in handlers.
A new router module is an orchestrator commit to `NEW_ROUTER_MODULES`.

**A job.** Module `worker/jobs/<name>.py` with `async def run(ctx)`, setting
`TASK_<NAME>_ENABLED: bool = False` in your config section, one
`Job("<name>", run, interval_s, flag="<NAME>")` line in `scheduler.build_jobs`.
Registered jobs: payment_recovery, autopay, panel_sync, device_cleanup,
obhod_lifecycle, reminders, grace, panel_health, sun718_revert,
broadcast_resume, and the 2.x expiry_notifier / reconciler (retire in 3.0.1). The scheduler never overlaps a job with itself and runs
jobs only on the leader (`scheduler:leader` in Redis; Redis down = run).

**An API route.** Module `api/routes/<name>.py` with an `APIRouter`, included
in `api/app.py` (orchestrator commit). Use `get_container()` for ports. The
`/internal/site/*` contract is frozen (golden files in `tests/contracts/golden`).

**An admin notification.** `await notifier.notify_admins(AdminTopic.X, text)`.
Plain text is escaped; pass `html=True` only for text built from constants and
`h()`. Use `dedup_key`/`dedup_ttl` for anything that can repeat.

## Migrations

Naming: file `src/app/db/migrations/versions/r30_NN_<slug>.py`, revision id
`r30_NN_<slug>` (fits alembic_version varchar(32)), linear chain, exactly one
head (CI fails otherwise).

- `r30_01_additive` (Foundation): ADD COLUMN IF NOT EXISTS ... NULL and CREATE
  TABLE only if missing. Safe while 2.1.1 runs.
- `r30_02_*` constraints (NOT VALID, then VALIDATE), `r30_03_*` data
  backfills, `r30_04_*` drops (3.0.1), `r30_05+` requests from streams: all
  written by stream F only. Other streams ask F; they never add revisions.
- Every migration: model in `db/models.py` updated in the same commit,
  `alembic upgrade head && alembic check` clean on an empty Postgres 14.

## Tests

- `tests/fakes/`: FakeRemna / FakeRemnaGateway, FakePaymentGateway,
  FakeStarsGateway, RecordingNotifier, FakeRedis, FakeClock, bot
  (RecordingSession, make_bot, message_update, callback_update).
- `tests/flows/`: fixture `flow` feeds updates through the real dispatcher
  (`flow.send("/start")`, `flow.press("back_to_main")`, `flow.session.calls`).
- `tests/invariants/`: manual squads, device limit, prices, sub_kind, no
  exception text to users, no subscription URLs in logs, no letter U+0451.
  Add your new top-level modules to `NEW_LAYER` there.
- `tests/contracts/`: golden JSON of the site API; `UPDATE_GOLDEN=1` rewrites
  (only for an agreed contract change).
- Per-stream folders: `tests/{money,panel,events,ui,growth,data}/`.
- CI: `pytest tests/ -m "not integration"`, ruff, single alembic head,
  upgrade/check/downgrade/upgrade on Postgres, integration tests.
