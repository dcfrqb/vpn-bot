#!/usr/bin/env python3
"""
delete_user.py — безопасное полное удаление пользователя из системы.

Удаляет:
  1. Пользователя из Remnawave (API DELETE)
  2. Все записи из БД в правильном порядке (каскад через telegram_users)
  3. Redis-ключи expiry_notice и subscription_cache для этого tg_id

Запуск (в контейнере или с PYTHONPATH=src):
  python scripts/delete_user.py <telegram_id>

Пример:
  docker compose exec bot python /app/scripts/delete_user.py 5628460233

Безопасность:
  - Требует явного подтверждения (введи telegram_id ещё раз)
  - Выводит всё что найдено ПЕРЕД удалением
  - Ничего не удаляет если Remnawave API недоступен и --force не передан
"""
import asyncio
import sys
import os

# Allow running as script with PYTHONPATH or from container
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))


async def audit_user(telegram_id: int) -> dict:
    """Collect all DB records for the user. Returns summary dict."""
    from app.db.session import SessionLocal
    from app.db.models import TelegramUser, Subscription, Payment
    from sqlalchemy import select, text

    result = {
        "telegram_user": None,
        "subscriptions": [],
        "payments": [],
        "remna_users": [],
        "redis_keys": [],
    }

    async with SessionLocal() as session:
        # telegram_users
        tu = await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == telegram_id))
        tu = tu.scalar_one_or_none()
        result["telegram_user"] = tu

        if tu is None:
            return result

        # subscriptions
        subs = await session.execute(
            select(Subscription).where(Subscription.telegram_user_id == telegram_id)
        )
        result["subscriptions"] = subs.scalars().all()

        # payments
        pmts = await session.execute(
            select(Payment).where(Payment.telegram_user_id == telegram_id)
        )
        result["payments"] = pmts.scalars().all()

        # remna_users linked via remna_user_id on telegram_users
        if tu.remna_user_id:
            ru = await session.execute(
                text("SELECT remna_id, username, created_at FROM remna_users WHERE remna_id = :rid"),
                {"rid": tu.remna_user_id},
            )
            result["remna_users"] = ru.fetchall()

        # also check remna_users referenced by subscriptions (might differ)
        for sub in result["subscriptions"]:
            if sub.remna_user_id and sub.remna_user_id != (tu.remna_user_id or ""):
                ru2 = await session.execute(
                    text("SELECT remna_id, username, created_at FROM remna_users WHERE remna_id = :rid"),
                    {"rid": sub.remna_user_id},
                )
                rows = ru2.fetchall()
                result["remna_users"].extend(rows)

    return result


async def delete_user(telegram_id: int, force: bool = False) -> bool:
    """
    Fully delete a user from the system:
    1. Remnawave API DELETE for each remna_user_id
    2. DB cascade delete via telegram_users
    3. Redis cache clear

    Returns True on success.
    """
    from app.db.session import SessionLocal
    from app.db.models import TelegramUser
    from app.remnawave.client import RemnaClient
    from app.services.cache import get_redis_client
    from sqlalchemy import select, text

    # --- 1. Find remna UUIDs ---
    remna_ids = set()
    async with SessionLocal() as session:
        tu = await session.execute(select(TelegramUser).where(TelegramUser.telegram_id == telegram_id))
        tu = tu.scalar_one_or_none()
        if tu is None:
            print(f"[INFO] telegram_id={telegram_id} not found in telegram_users")
            return False
        if tu.remna_user_id:
            remna_ids.add(tu.remna_user_id)

        # subscriptions may reference a different remna_id
        rows = await session.execute(
            text("SELECT remna_user_id FROM subscriptions WHERE telegram_user_id = :tid AND remna_user_id IS NOT NULL"),
            {"tid": telegram_id},
        )
        for row in rows:
            remna_ids.add(row[0])

    # --- 2. Delete from Remnawave ---
    client = RemnaClient()
    try:
        for rid in remna_ids:
            print(f"  [Remnawave] DELETE {rid} ...", end=" ")
            try:
                resp = await client.delete_user(rid)
                print(f"OK: {resp}")
            except Exception as e:
                msg = str(e)
                if "A063" in msg or "not found" in msg.lower():
                    print(f"already gone (A063)")
                elif force:
                    print(f"WARN (--force): {msg}")
                else:
                    print(f"ERROR: {msg}")
                    print("  Aborting. Use --force to skip Remnawave errors.")
                    return False
    finally:
        await client.close()

    # --- 3. DB cascade delete ---
    async with SessionLocal() as session:
        # Delete both ghost remna_users and linked ones
        await session.execute(
            text("DELETE FROM remna_users WHERE remna_id = ANY(:ids)"),
            {"ids": list(remna_ids)},
        )
        # Delete telegram_user → cascades payments, subscriptions, access_requests
        result = await session.execute(
            text("DELETE FROM telegram_users WHERE telegram_id = :tid RETURNING telegram_id"),
            {"tid": telegram_id},
        )
        deleted = result.fetchone()
        await session.commit()
        if deleted:
            print(f"  [DB] telegram_users + cascade deleted for telegram_id={telegram_id}")
        else:
            print(f"  [DB] telegram_users row not found (cascade not triggered)")

    # Also clean up orphan ghost remna_users that might have no telegram_user link
    async with SessionLocal() as session:
        orphan_check = await session.execute(
            text("SELECT COUNT(*) FROM remna_users WHERE remna_id = ANY(:ids)"),
            {"ids": list(remna_ids)},
        )
        remaining = orphan_check.scalar()
        if remaining:
            await session.execute(
                text("DELETE FROM remna_users WHERE remna_id = ANY(:ids)"),
                {"ids": list(remna_ids)},
            )
            await session.commit()
            print(f"  [DB] cleaned {remaining} orphan remna_users records")

    # --- 4. Redis cache ---
    redis = get_redis_client()
    if redis:
        try:
            pattern = f"*{telegram_id}*"
            keys = await redis.keys(pattern)
            if keys:
                await redis.delete(*keys)
                print(f"  [Redis] deleted {len(keys)} keys: {[k.decode() if isinstance(k, bytes) else k for k in keys]}")
            else:
                print(f"  [Redis] no keys found for {telegram_id}")
        except Exception as e:
            print(f"  [Redis] WARN: {e}")

    return True


def print_audit(telegram_id: int, audit: dict):
    tu = audit["telegram_user"]
    print(f"\n{'='*60}")
    print(f"AUDIT: telegram_id={telegram_id}")
    print(f"{'='*60}")
    if tu is None:
        print("  telegram_users: NOT FOUND")
        return

    print(f"  telegram_users:")
    print(f"    telegram_id={tu.telegram_id}, username={tu.username}")
    print(f"    remna_user_id={tu.remna_user_id}, created={tu.created_at}")

    print(f"  remna_users ({len(audit['remna_users'])} record(s)):")
    for r in audit["remna_users"]:
        print(f"    remna_id={r[0]}, username={r[1]}, created={r[2]}")

    print(f"  subscriptions ({len(audit['subscriptions'])} record(s)):")
    for s in audit["subscriptions"]:
        print(f"    id={s.id}, plan={s.plan_code}, active={s.active}, valid_until={s.valid_until}, remna_user_id={s.remna_user_id}")

    print(f"  payments ({len(audit['payments'])} record(s)):")
    for p in audit["payments"]:
        print(f"    id={p.id}, provider={p.provider}, external_id={p.external_id}, amount={p.amount}, status={p.status}")

    print(f"{'='*60}\n")


async def main():
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if not a.startswith("-")]

    if not args:
        print("Usage: python scripts/delete_user.py <telegram_id> [--force]")
        sys.exit(1)

    try:
        telegram_id = int(args[0])
    except ValueError:
        print(f"ERROR: invalid telegram_id '{args[0]}'")
        sys.exit(1)

    # Audit first
    print(f"\nAuditing user {telegram_id}...")
    audit = await audit_user(telegram_id)
    print_audit(telegram_id, audit)

    if audit["telegram_user"] is None:
        print("Nothing to delete.")
        sys.exit(0)

    # Confirm
    print(f"Type the telegram_id again to confirm deletion: ", end="")
    confirm = input().strip()
    if confirm != str(telegram_id):
        print("Aborted.")
        sys.exit(1)

    print(f"\nDeleting user {telegram_id}...")
    success = await delete_user(telegram_id, force=force)
    if success:
        print(f"\nUser {telegram_id} fully deleted.")
    else:
        print(f"\nDeletion failed or incomplete.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
