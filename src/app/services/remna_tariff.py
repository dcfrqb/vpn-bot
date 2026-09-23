"""Применение тарифа к юзеру Remnawave без затирания ручных настроек (хотфикс 2.1).

Раньше каждая оплата/продление/resync/revert делала
  activeInternalSquads = [сквад тарифа]   (полная замена)
  hwidDeviceLimit      = лимит тарифа     (в том числе понижение)
и молча сносила ручные сквады (*-friend, arcadia, us-2, esp, full, *-m) и
поднятые вручную лимиты устройств.

Политика (одна на все пути выдачи):

Сквады основного юзера:
  - бот управляет только тарифными сквадами каталога (MANAGED: basic, premium,
    lite, standard, pro — см. core/plans.PLAN_CATALOG);
  - из текущих сквадов убираются только MANAGED-сквады других тарифов,
    добавляется сквад оплаченного тарифа, все остальное остается как было;
  - сквад obhod на основном юзере бот не ставит и не трогает (obhod живет на
    отдельном obhod-юзере, им управляет obhod_service);
  - ручные сквады НИКОГДА не снимаются, это явное правило, а не следствие
    «не входит в каталог»: суффикс -m (m = manual, ручные плательщики:
    lite-m, standard-m, pro-m, premium-m — решение владельца 23.09.2026),
    суффикс -friend и arcadia. Оплата, продление, даунгрейд, resync их
    сохраняют (is_manual_squad_name).

Лимит устройств (hwidDeviceLimit), «никогда не понижать»:
  - текущее значение N > 0  -> max(N, лимит тарифа);
  - текущее 0               -> не трогаем (выставлено вручную);
  - текущее NULL (fallback панели): если у юзера есть сквады вне MANAGED
    (ручная настройка) -> не трогаем; иначе ставим лимит тарифа (это юзер,
    созданный ботом без лимита при /start).
  Следствие: при даунгрейде (Pro 10 -> Lite 2) лимит остается 10. Это
  осознанная цена за то, чтобы не резать ручные лимиты; решать в 3.0.

Статус DISABLED (фикс-раунд 2, ревью N2): возврат больше не отключает юзера,
а ставит expireAt = сейчас; панель сама переводит его в EXPIRED, и перенос
expireAt в будущее оживляет его штатно. DISABLED теперь означает «отключен
руками админа в панели» (или ранней версией 2.1), и бот его сам не включает:
  - явная выдача (оплата, промо, админ-грант) вызывается с
    refuse_if_disabled=True: для DISABLED юзера ничего не пишется и
    бросается RemnaUserDisabledError. Оплата уходит на ручную проверку
    (кнопки «Одобрить»/«Отклонить»), промо и админ-грант не выдаются, админам
    один алерт;
  - enable_if_disabled=True только там, где решение принял человек: оплата,
    одобренная админом кнопкой на ревью. Тогда после PATCH юзер включается;
  - resync реконсилера и откат sun718 (оба флага False) пишут как раньше и
    статус не трогают.

Все изменения уходят ОДНИМ PATCH (expireAt + сквады + лимит), чтобы не было
полуприменного состояния. Любая ошибка (юзер не читается, сквад не найден,
PATCH упал) -> RemnaTariffError; вызывающий код обязан считать выдачу
несостоявшейся (не synced).
"""
from typing import Any, Dict, Iterable, List, Optional, Set

from app.core.plans import PLAN_CATALOG, get_plan_device_limit, get_plan_squad
from app.logger import logger


class RemnaTariffError(Exception):
    """Тариф не применен к юзеру Remnawave (нужен retry / внимание админа)."""


class RemnaUserDisabledError(RemnaTariffError):
    """Юзер отключен в панели вручную (DISABLED): автоматически не включаем,
    выдача не выполнена, решает админ (ревью N2)."""


# Ручные сквады: бот их не ставит и не снимает (см. docstring модуля).
MANUAL_SQUAD_SUFFIXES = ("-m", "-friend")
MANUAL_SQUAD_NAMES = frozenset({"arcadia"})


def is_manual_squad_name(name: Optional[str]) -> bool:
    """Сквад, выданный вручную (ручной плательщик *-m, друг *-friend, arcadia)."""
    n = (name or "").strip().lower()
    return n in MANUAL_SQUAD_NAMES or any(n.endswith(suf) for suf in MANUAL_SQUAD_SUFFIXES)


def managed_tariff_squad_names() -> Set[str]:
    """Имена сквадов, которыми управляет бот на основном юзере.

    Только сквады каталога, и никогда ручные (даже если такой сквад когда-то
    попадет в каталог по ошибке).
    """
    return {
        meta["squad"] for meta in PLAN_CATALOG.values()
        if meta.get("squad") and not is_manual_squad_name(meta["squad"])
    }


def extract_squad_uuids(user_raw: Dict[str, Any]) -> List[str]:
    """uuid'ы activeInternalSquads из ответа Remnawave (dict или str элементы)."""
    out: List[str] = []
    for item in (user_raw or {}).get("activeInternalSquads") or []:
        if isinstance(item, dict):
            uid = item.get("uuid")
        else:
            uid = item
        if uid and uid not in out:
            out.append(str(uid))
    return out


def merge_tariff_squads(
    current: Iterable[str], target_uuid: str, managed_uuids: Iterable[str]
) -> List[str]:
    """Оставляет все не-managed сквады, заменяет managed на target_uuid."""
    managed = set(managed_uuids)
    result = [u for u in current if u not in managed]
    if target_uuid not in result:
        result.append(target_uuid)
    return result


def resolve_device_limit(
    current: Optional[int], plan_limit: int, has_foreign_squads: bool
) -> Optional[int]:
    """Новый hwidDeviceLimit или None (не трогать). См. политику в docstring модуля."""
    if current is None:
        return None if has_foreign_squads else int(plan_limit)
    try:
        current_int = int(current)
    except (TypeError, ValueError):
        return None
    if current_int == 0:
        return None
    return max(current_int, int(plan_limit))


def _unwrap(data: Any) -> Dict[str, Any]:
    raw = data.get("response", data) if isinstance(data, dict) else {}
    return raw if isinstance(raw, dict) else {}


async def apply_tariff_to_remna_user(
    client,
    remna_user_id: str,
    plan_code: Optional[str],
    *,
    expire_at: Any = None,
    set_device_limit: bool = True,
    user_data: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    enable_if_disabled: bool = False,
    refuse_if_disabled: bool = False,
) -> Dict[str, Any]:
    """Применяет тариф к основному юзеру Remnawave одним PATCH.

    expire_at — новое значение expireAt (str/datetime) или None (не менять).
    set_device_limit=False — не трогать лимит устройств (sun718 revert).
    enable_if_disabled=True — выдача, одобренная админом: юзер в статусе DISABLED
    включается (enable_user) после PATCH. Работает только вместе с expire_at.
    refuse_if_disabled=True — автоматическая выдача: для DISABLED юзера ничего
    не пишем и бросаем RemnaUserDisabledError (enable_if_disabled важнее).
    user_data — уже прочитанный ответ GET /api/users/{id} (экономим запрос).

    Возвращает payload, который ушел в PATCH ({} если менять было нечего).
    Бросает RemnaTariffError при любой проблеме.
    """
    squad_name = get_plan_squad(plan_code)
    if not squad_name:
        raise RemnaTariffError(f"unknown plan_code={plan_code!r}: нет сквада в каталоге")

    if user_data is None:
        try:
            user_data = await client.get_user_by_id(str(remna_user_id))
        except Exception as e:
            raise RemnaTariffError(f"get_user_by_id({remna_user_id}) failed: {e}") from e
    raw = _unwrap(user_data)
    if not raw:
        raise RemnaTariffError(f"remna user {remna_user_id} not readable (empty response)")

    is_disabled = str(raw.get("status") or "").upper() == "DISABLED"
    if is_disabled and refuse_if_disabled and not enable_if_disabled:
        logger.warning(
            f"[{trace_id}] remna_tariff: user {remna_user_id} is DISABLED in panel, "
            f"auto grant refused plan={plan_code}"
        )
        raise RemnaUserDisabledError(
            f"remna user {remna_user_id} is DISABLED (отключен вручную), автоматически не включаем"
        )

    try:
        squads = await client.list_internal_squads()
    except Exception as e:
        raise RemnaTariffError(f"internal squads lookup failed: {e}") from e
    name_to_uuid = {s.get("name"): s.get("uuid") for s in squads if isinstance(s, dict)}
    target_uuid = name_to_uuid.get(squad_name)
    if not target_uuid:
        raise RemnaTariffError(
            f"squad_not_found: plan={plan_code!r} squad={squad_name!r} (проверьте сквады в Remnawave)"
        )
    managed_names = managed_tariff_squad_names()
    managed_uuids = {name_to_uuid[n] for n in managed_names if name_to_uuid.get(n)}

    current_uuids = extract_squad_uuids(raw)
    new_uuids = merge_tariff_squads(current_uuids, target_uuid, managed_uuids)
    foreign = [u for u in current_uuids if u not in managed_uuids]

    payload: Dict[str, Any] = {}
    if expire_at is not None:
        payload["expire_at"] = expire_at
    if set(new_uuids) != set(current_uuids):
        payload["activeInternalSquads"] = new_uuids
    if set_device_limit:
        current_limit = raw.get("hwidDeviceLimit")
        new_limit = resolve_device_limit(
            current_limit, get_plan_device_limit(plan_code), has_foreign_squads=bool(foreign)
        )
        if new_limit is not None and new_limit != current_limit:
            payload["hwid_device_limit"] = new_limit

    needs_enable = enable_if_disabled and expire_at is not None and is_disabled

    if not payload and not needs_enable:
        logger.info(
            f"[{trace_id}] remna_tariff: nothing to change remna_user_id={remna_user_id} plan={plan_code}"
        )
        return {}

    if payload:
        try:
            await client.update_user(str(remna_user_id), **payload)
        except Exception as e:
            raise RemnaTariffError(f"update_user({remna_user_id}) failed: {e}") from e

    if needs_enable:
        try:
            await client.enable_user(str(remna_user_id))
        except Exception as e:
            raise RemnaTariffError(f"enable_user({remna_user_id}) failed: {e}") from e
        logger.warning(
            f"[{trace_id}] remna_tariff: user {remna_user_id} was DISABLED, re-enabled "
            f"(approved by admin) plan={plan_code}"
        )

    logger.info(
        f"[{trace_id}] remna_tariff applied: remna_user_id={remna_user_id} plan={plan_code} "
        f"expire={payload.get('expire_at')} squads={len(current_uuids)}->{len(new_uuids)} "
        f"kept_foreign={len(foreign)} limit={payload.get('hwid_device_limit', 'unchanged')}"
    )
    return payload

