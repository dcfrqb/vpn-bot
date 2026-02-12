"""Сервис синхронизации пользователей и подписок с Remna"""
from datetime import datetime
from typing import Optional
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
import time

from app.remnawave.client import RemnaClient, RemnaUser, RemnaSubscription
from app.repositories.user_repo import UserRepo
from app.repositories.subscription_repo import SubscriptionRepo
from app.db.session import SessionLocal
from app.logger import logger


@dataclass
class SyncResult:
    """Результат синхронизации"""
    is_new_user_created: bool  # Был ли создан новый пользователь в Remna
    user_remna_uuid: Optional[str]  # UUID пользователя в Remna
    subscription_status: str  # "active", "expired", "none"
    expires_at: Optional[datetime]  # Дата истечения подписки
    source: str = "remna"  # Источник данных


class RemnaUnavailableError(Exception):
    """Ошибка недоступности Remna API"""
    pass


class SyncService:
    """Сервис синхронизации пользователей и подписок с Remna"""
    
    def __init__(self, remna_client: Optional[RemnaClient] = None):
        self.remna_client = remna_client or RemnaClient()
    
    async def sync_user_and_subscription(
        self,
        telegram_id: int,
        tg_name: str,
        use_fallback: bool = False,
        use_cache: bool = True,
        force_sync: bool = False,
        force_remna: bool = False
    ) -> SyncResult:
        """
        Синхронизирует пользователя и подписку с Remna.
        
        Алгоритм:
        1. Проверяет кэш (если use_cache=True и не force_sync и не force_remna)
        2. Если кэш есть - возвращает из кэша БЕЗ запроса к Remna
        3. Если кэша нет - выполняет синхронизацию с Remna
        4. Сохраняет результат в кэш
        
        Args:
            telegram_id: Telegram ID пользователя
            tg_name: Имя пользователя из Telegram (для создания)
            use_fallback: Использовать ли данные из БД как fallback при недоступности Remna
            use_cache: Использовать ли кэш (по умолчанию True)
            force_sync: Принудительная синхронизация, игнорирует кэш (по умолчанию False)
            force_remna: Принудительная синхронизация с Remna API, игнорирует кэш и fallback (по умолчанию False)
        
        Returns:
            SyncResult с информацией о синхронизации
        
        Raises:
            RemnaUnavailableError: если Remna недоступна и (use_fallback=False или force_remna=True)
        """
        if not SessionLocal:
            raise RuntimeError("БД не настроена")
        
        start_time = time.time()
        cache_hit = False
        
        # Если force_remna=True, принудительно отключаем кэш и fallback
        if force_remna:
            use_cache = False
            use_fallback = False
            force_sync = True
            logger.info(f"Принудительная синхронизация с Remna API для {telegram_id} (force_remna=True)")
        
        # Шаг 1: Проверяем кэш (если не принудительная синхронизация)
        if use_cache and not force_sync:
            try:
                from app.services.cache import get_cached_sync_result
                cached = await get_cached_sync_result(telegram_id)
                if cached:
                    cache_hit = True
                    # Восстанавливаем SyncResult из кэша
                    expires_at = None
                    if cached.get('expires_at'):
                        try:
                            if isinstance(cached['expires_at'], str):
                                expires_at = datetime.fromisoformat(cached['expires_at'])
                            else:
                                expires_at = cached['expires_at']
                        except Exception as e:
                            logger.debug(f"Ошибка парсинга expires_at из кэша: {e}")
                    
                    elapsed_ms = (time.time() - start_time) * 1000
                    logger.info(
                        f"SyncResult получен из кэша для {telegram_id} "
                        f"(время: {elapsed_ms:.2f}мс, status={cached.get('status')})"
                    )
                    
                    return SyncResult(
                        is_new_user_created=False,
                        user_remna_uuid=cached.get('remna_uuid'),
                        subscription_status=cached.get('status', 'none'),
                        expires_at=expires_at,
                        source=cached.get('source', 'cache')
                    )
            except Exception as e:
                logger.debug(f"Ошибка при получении из кэша для {telegram_id}: {e}")
        
        # Кэш промах или принудительная синхронизация - выполняем запрос к Remna
        remna_start_time = time.time()
        logger.info(f"Начало синхронизации пользователя telegram_id={telegram_id} (cache_miss={not cache_hit})")
        
        # Шаг 2: Пытаемся найти пользователя в Remna
        remna_user: Optional[RemnaUser] = None
        remna_subscription: Optional[RemnaSubscription] = None
        is_new_user_created = False
        
        try:
            # Пробуем получить пользователя с подпиской одним запросом
            result = await self.remna_client.get_user_with_subscription_by_telegram_id(telegram_id)
            remna_elapsed_ms = (time.time() - remna_start_time) * 1000
            logger.info(f"Remna API запрос для {telegram_id} выполнен за {remna_elapsed_ms:.2f}мс")
            
            if result:
                remna_user, remna_subscription = result
                logger.info(f"Найден пользователь в Remna: uuid={remna_user.uuid}, telegram_id={telegram_id}")
            else:
                logger.info(f"Пользователь с telegram_id={telegram_id} не найден в Remna через /api/users, проверяю БД на наличие UUID...")
                # Fallback: проверяем БД на наличие remna_user_id
                if SessionLocal:
                    try:
                        async with SessionLocal() as session:
                            from app.db.models import TelegramUser
                            from sqlalchemy import select
                            user_result = await session.execute(
                                select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
                            )
                            telegram_user = user_result.scalar_one_or_none()
                            
                            if telegram_user and telegram_user.remna_user_id:
                                logger.info(f"Найден remna_user_id={telegram_user.remna_user_id} в БД для telegram_id={telegram_id}, получаю пользователя напрямую по UUID...")
                                try:
                                    # Получаем пользователя напрямую по UUID
                                    user_data = await self.remna_client.get_user_by_id(telegram_user.remna_user_id)
                                    
                                    # Обрабатываем ответ
                                    if isinstance(user_data, dict):
                                        if 'response' in user_data:
                                            user_data = user_data['response']
                                        
                                        uuid = user_data.get('uuid') or user_data.get('id')
                                        if uuid:
                                            username = user_data.get('username')
                                            name = user_data.get('name') or user_data.get('displayName') or username
                                            
                                            remna_user = RemnaUser(
                                                uuid=str(uuid),
                                                telegram_id=telegram_id,
                                                username=username,
                                                name=name,
                                                raw_data=user_data
                                            )
                                            
                                            # Извлекаем информацию о подписке
                                            expire_at_raw = user_data.get('expireAt') or user_data.get('expires_at')
                                            expire_dt = None
                                            is_active = False
                                            
                                            if expire_at_raw:
                                                try:
                                                    if isinstance(expire_at_raw, str):
                                                        expire_str = expire_at_raw.replace('Z', '+00:00')
                                                        if '+' not in expire_str and '-' not in expire_str[-6:]:
                                                            expire_str += '+00:00'
                                                        expire_dt = datetime.fromisoformat(expire_str)
                                                        if expire_dt.tzinfo:
                                                            expire_dt = expire_dt.replace(tzinfo=None)
                                                    elif isinstance(expire_at_raw, (int, float)):
                                                        expire_dt = datetime.fromtimestamp(expire_at_raw)
                                                    elif isinstance(expire_at_raw, datetime):
                                                        expire_dt = expire_at_raw
                                                        if expire_dt.tzinfo:
                                                            expire_dt = expire_dt.replace(tzinfo=None)
                                                    
                                                    if expire_dt:
                                                        is_active = expire_dt > datetime.utcnow()
                                                except Exception as e:
                                                    logger.warning(f"Ошибка парсинга expireAt для пользователя {uuid}: {e}")
                                            
                                            plan = user_data.get('plan') or user_data.get('planCode') or user_data.get('plan_code')
                                            
                                            remna_subscription = RemnaSubscription(
                                                active=is_active,
                                                expires_at=expire_dt,
                                                plan=plan,
                                                raw_data=user_data.get('subscription', user_data)
                                            )
                                            
                                            logger.info(f"✅ Пользователь получен напрямую по UUID: uuid={uuid}, telegram_id={telegram_id}, active={is_active}")
                                except Exception as uuid_e:
                                    logger.warning(f"Не удалось получить пользователя по UUID {telegram_user.remna_user_id}: {uuid_e}")
                    except Exception as db_e:
                        logger.debug(f"Ошибка при проверке БД на наличие UUID: {db_e}")
        
        except Exception as e:
            remna_elapsed_ms = (time.time() - remna_start_time) * 1000
            error_msg = str(e).lower()
            error_type = type(e).__name__
            
            # Проверяем, это таймаут/сеть/5xx ошибка или ошибка API
            is_network_error = (
                'timeout' in error_msg or 
                'connection' in error_msg or 
                'network' in error_msg or
                'connect' in error_msg or
                error_type in ('TimeoutException', 'ConnectTimeout', 'ReadTimeout', 'ConnectError')
            )
            
            # Проверяем 5xx ошибки (серверные ошибки)
            is_server_error = (
                '500' in error_msg or
                '502' in error_msg or
                '503' in error_msg or
                '504' in error_msg or
                'service unavailable' in error_msg or
                'bad gateway' in error_msg or
                'gateway timeout' in error_msg
            )
            
            if is_network_error or is_server_error:
                logger.warning(
                    f"Remna API недоступна ({'сеть/таймаут' if is_network_error else '5xx ошибка'}) "
                    f"для {telegram_id}: {e} (время запроса: {remna_elapsed_ms:.2f}мс, force_remna={force_remna})"
                )
                # Если force_remna=True, НЕ используем fallback - пробрасываем ошибку
                if force_remna:
                    logger.error(
                        f"Принудительная синхронизация с Remna не удалась для {telegram_id}: "
                        f"Remna API недоступна, fallback отключен"
                    )
                    raise RemnaUnavailableError(f"Remna API недоступна: {e}")
                
                if use_fallback:
                    logger.info(f"Используем fallback для пользователя {telegram_id}")
                    try:
                        fallback_result = await self._fallback_sync(telegram_id)
                        # Сохраняем fallback результат в кэш на короткое время (60 сек)
                        if use_cache:
                            try:
                                await self._save_sync_result_to_cache(telegram_id, fallback_result, ttl=60)
                            except Exception as cache_e:
                                logger.debug(f"Ошибка сохранения fallback в кэш: {cache_e}")
                        return fallback_result
                    except Exception as fallback_e:
                        logger.error(f"Ошибка fallback синхронизации для {telegram_id}: {fallback_e}")
                        # Если fallback тоже не сработал, возвращаем минимальный результат
                        return SyncResult(
                            is_new_user_created=False,
                            user_remna_uuid=None,
                            subscription_status="none",
                            expires_at=None,
                            source="error_fallback"
                        )
                else:
                    raise RemnaUnavailableError(f"Remna API недоступна: {e}")
            else:
                # Другая ошибка API (4xx, валидация и т.д.) - пробрасываем
                logger.error(
                    f"Ошибка Remna API при поиске пользователя {telegram_id}: {e} "
                    f"(время запроса: {remna_elapsed_ms:.2f}мс, тип: {error_type})"
                )
                raise
        
        # Шаг 3: Если пользователь найден - обновляем БД данными из Remna
        if remna_user:
            sync_result = await self._sync_existing_user(
                telegram_id=telegram_id,
                tg_name=tg_name,
                remna_user=remna_user,
                remna_subscription=remna_subscription
            )
        else:
            # Шаг 4: Пользователь не найден - создаем в Remna
            sync_result = await self._create_new_user(
                telegram_id=telegram_id,
                tg_name=tg_name
            )
        
        # Сохраняем результат в кэш только если он пришел из Remna (не из fallback)
        # При force_remna НЕ кэшируем, т.к. это принудительная проверка (например, для /friend)
        # которая должна всегда обращаться к Remna напрямую
        if use_cache and sync_result.source == "remna" and not force_remna:
            try:
                await self._save_sync_result_to_cache(telegram_id, sync_result)
                logger.debug(f"SyncResult сохранен в кэш для {telegram_id} (source={sync_result.source})")
            except Exception as e:
                logger.debug(f"Ошибка сохранения SyncResult в кэш для {telegram_id}: {e}")
        
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Синхронизация завершена для {telegram_id}: "
            f"status={sync_result.subscription_status}, "
            f"is_new={sync_result.is_new_user_created}, "
            f"remna_uuid={sync_result.user_remna_uuid}, "
            f"общее время: {elapsed_ms:.2f}мс, "
            f"cache_hit={cache_hit}, "
            f"force_remna={force_remna}, "
            f"source={sync_result.source}"
        )
        
        return sync_result
    
    async def _save_sync_result_to_cache(self, telegram_id: int, sync_result: SyncResult, ttl: Optional[int] = None):
        """Сохраняет SyncResult в кэш"""
        from app.services.cache import set_cached_sync_result, SYNC_CACHE_TTL
        
        cache_data = {
            'telegram_id': telegram_id,
            'remna_uuid': sync_result.user_remna_uuid,
            'has_subscription': sync_result.subscription_status in ('active', 'expired'),
            'status': sync_result.subscription_status,
            'expires_at': sync_result.expires_at,
            'updated_at': datetime.utcnow(),
            'source': sync_result.source
        }
        
        await set_cached_sync_result(telegram_id, cache_data, ttl=ttl or SYNC_CACHE_TTL)
    
    async def _sync_existing_user(
        self,
        telegram_id: int,
        tg_name: str,
        remna_user: RemnaUser,
        remna_subscription: Optional[RemnaSubscription]
    ) -> SyncResult:
        """Синхронизирует существующего пользователя из Remna в БД"""
        async with SessionLocal() as session:
            try:
                user_repo = UserRepo(session)
                sub_repo = SubscriptionRepo(session)
                
                # Обновляем/создаем RemnaUser в БД
                await user_repo.upsert_remna_user(
                    remna_id=remna_user.uuid,
                    defaults={
                        'username': remna_user.username,
                        'raw_data': remna_user.raw_data,
                        'last_synced_at': datetime.utcnow()
                    }
                )
                
                # Обновляем/создаем TelegramUser с привязкой к RemnaUser
                telegram_user = await user_repo.upsert_user_by_telegram_id(
                    telegram_id=telegram_id,
                    defaults={
                        'username': remna_user.username,
                        'first_name': tg_name.split()[0] if tg_name else None,
                        'remna_user_id': remna_user.uuid,
                        'last_activity_at': datetime.utcnow()
                    }
                )
                
                # Синхронизируем подписку
                subscription_status = "none"
                expires_at = None
                
                if remna_subscription:
                    # Есть информация о подписке
                    expires_at = remna_subscription.expires_at
                    now = datetime.utcnow()
                    
                    # Логируем детали для отладки
                    logger.info(
                        f"Проверка подписки для {telegram_id}: "
                        f"remna_subscription.active={remna_subscription.active}, "
                        f"expires_at={expires_at}, "
                        f"now={now}, "
                        f"expires_at > now={expires_at > now if expires_at else 'N/A'}"
                    )
                    
                    # Определяем статус: активна, если expires_at в будущем (независимо от remna_subscription.active)
                    # Это важно, т.к. Remna API может вернуть active=False, но expires_at в будущем
                    if expires_at and expires_at > now:
                        subscription_status = "active"
                        logger.info(f"Подписка для {telegram_id} определена как ACTIVE (expires_at={expires_at} > now={now})")
                    elif expires_at and expires_at <= now:
                        subscription_status = "expired"
                        logger.info(f"Подписка для {telegram_id} определена как EXPIRED (expires_at={expires_at} <= now={now})")
                    elif remna_subscription.active:
                        # Если expires_at нет, но active=True - считаем активной
                        subscription_status = "active"
                        logger.info(f"Подписка для {telegram_id} определена как ACTIVE (active=True, но expires_at отсутствует)")
                    else:
                        subscription_status = "none"
                        logger.info(f"Подписка для {telegram_id} определена как NONE (нет expires_at и active=False)")
                    
                    # Определяем plan_code из данных Remna
                    raw_plan = remna_subscription.plan
                    plan_code = (raw_plan or "unknown").lower().strip() if raw_plan else "unknown"
                    # Нормализация: используем единый справочник тарифов
                    from app.core.plans import get_plan_name
                    plan_name = get_plan_name(plan_code if plan_code != "unknown" else None)
                    
                    # Определяем active на основе expires_at, а не remna_subscription.active
                    # Это важно для корректной синхронизации с панелью
                    is_active = expires_at and expires_at > datetime.utcnow()
                    
                    await sub_repo.upsert_subscription(
                        telegram_user_id=telegram_id,
                        defaults={
                            'remna_user_id': remna_user.uuid,
                            'plan_code': plan_code,
                            'plan_name': plan_name,
                            'active': is_active,  # Используем вычисленное значение, а не remna_subscription.active
                            'valid_until': expires_at,
                            'config_data': remna_subscription.raw_data
                        }
                    )
                else:
                    # Нет подписки в Remna - проверяем, есть ли в БД
                    existing_sub = await sub_repo.get_subscription_by_user_id(telegram_id)
                    if existing_sub:
                        # Деактивируем подписку в БД, т.к. в Remna её нет
                        await sub_repo.upsert_subscription(
                            telegram_user_id=telegram_id,
                            defaults={
                                'remna_user_id': remna_user.uuid,
                                'plan_code': existing_sub.plan_code,
                                'plan_name': existing_sub.plan_name,
                                'active': False,
                                'valid_until': None
                            }
                        )
                
                await session.commit()
                
                logger.info(
                    f"Синхронизирован пользователь {telegram_id}: "
                    f"remna_uuid={remna_user.uuid}, subscription_status={subscription_status}"
                )
                
                return SyncResult(
                    is_new_user_created=False,
                    user_remna_uuid=remna_user.uuid,
                    subscription_status=subscription_status,
                    expires_at=expires_at,
                    source="remna"
                )
            
            except Exception as e:
                await session.rollback()
                import traceback
                logger.error(
                    f"Ошибка при синхронизации пользователя {telegram_id}: {e}\n"
                    f"Traceback: {traceback.format_exc()}"
                )
                raise
    
    async def _create_new_user(
        self,
        telegram_id: int,
        tg_name: str
    ) -> SyncResult:
        """Создает нового пользователя в Remna и БД"""
        max_retries = 3
        remna_user: Optional[RemnaUser] = None
        is_new_user_created = False
        
        for attempt in range(max_retries):
            try:
                # Создаем пользователя в Remna
                remna_user = await self.remna_client.create_user_with_name(
                    telegram_id=telegram_id,
                    name=tg_name
                )
                
                logger.info(f"Создан пользователь в Remna: uuid={remna_user.uuid}, telegram_id={telegram_id}")
                is_new_user_created = True
                break
                
            except Exception as e:
                error_msg = str(e).lower()
                import traceback
                logger.warning(
                    f"Ошибка при создании пользователя {telegram_id} (попытка {attempt + 1}/{max_retries}): {e}\n"
                    f"Traceback: {traceback.format_exc()}"
                )
                
                # Проверяем, это конфликт (пользователь уже создан другим запросом)?
                if 'already exists' in error_msg or 'duplicate' in error_msg or '409' in error_msg:
                    logger.warning(f"Конфликт при создании пользователя {telegram_id} (попытка {attempt + 1}/{max_retries})")
                    # Пытаемся найти уже созданного пользователя
                    try:
                        remna_user = await self.remna_client.get_user_by_telegram_id(telegram_id)
                        if remna_user:
                            logger.info(f"Найден уже созданный пользователь в Remna: uuid={remna_user.uuid}")
                            is_new_user_created = False
                            break
                        else:
                            logger.warning(f"Пользователь не найден после конфликта, продолжаем попытки...")
                    except Exception as find_error:
                        logger.error(f"Ошибка при поиске пользователя после конфликта: {find_error}\nTraceback: {traceback.format_exc()}")
                
                # Проверяем, это ошибка валидации (400) - может быть проблема с форматом данных
                if '400' in error_msg or 'bad request' in error_msg:
                    logger.warning(f"Ошибка валидации при создании пользователя {telegram_id} в Remna (400). Пробуем найти существующего...")
                    try:
                        remna_user = await self.remna_client.get_user_by_telegram_id(telegram_id)
                        if remna_user:
                            logger.info(f"Найден существующий пользователь в Remna после 400 ошибки: uuid={remna_user.uuid}")
                            is_new_user_created = False
                            break
                    except Exception as find_error:
                        logger.warning(f"Не удалось найти пользователя после 400 ошибки: {find_error}")
                    # Продолжаем попытки, возможно это временная проблема
                
                if attempt == max_retries - 1:
                    # Последняя попытка - проверяем, нашли ли пользователя
                    if not remna_user:
                        # Пробуем еще раз найти пользователя перед финальной ошибкой
                        try:
                            remna_user = await self.remna_client.get_user_by_telegram_id(telegram_id)
                            if remna_user:
                                logger.info(f"Найден пользователь в Remna после всех попыток: uuid={remna_user.uuid}")
                                is_new_user_created = False
                            else:
                                # Если не удалось создать в Remna, создаем только в локальной БД
                                logger.warning(
                                    f"Не удалось создать пользователя {telegram_id} в Remna после {max_retries} попыток. "
                                    f"Создаем пользователя только в локальной БД."
                                )
                                # remna_user остается None, создадим пользователя только в БД
                                break
                        except Exception as final_error:
                            logger.warning(
                                f"Не удалось найти пользователя {telegram_id} в Remna: {final_error}. "
                                f"Создаем пользователя только в локальной БД."
                            )
                            # remna_user остается None, создадим пользователя только в БД
                            break
                
                # Ждем перед следующей попыткой
                import asyncio
                await asyncio.sleep(0.5 * (attempt + 1))
        
        # Если remna_user не был создан/найден, создаем пользователя только в локальной БД
        if not remna_user:
            logger.warning(
                f"Пользователь {telegram_id} не найден/создан в Remna. "
                f"Создаем пользователя только в локальной БД (без remna_uuid)."
            )
        
        # Создаем/обновляем в БД
        async with SessionLocal() as session:
            try:
                user_repo = UserRepo(session)
                sub_repo = SubscriptionRepo(session)
                
                # Если remna_user был создан/найден, создаем RemnaUser в БД
                if remna_user:
                    await user_repo.upsert_remna_user(
                        remna_id=remna_user.uuid,
                        defaults={
                            'username': remna_user.username,
                            'raw_data': remna_user.raw_data,
                            'last_synced_at': datetime.utcnow()
                        }
                    )
                    
                    # Создаем/обновляем TelegramUser с привязкой к RemnaUser
                    telegram_user = await user_repo.upsert_user_by_telegram_id(
                        telegram_id=telegram_id,
                        defaults={
                            'username': remna_user.username,
                            'first_name': tg_name.split()[0] if tg_name else None,
                            'remna_user_id': remna_user.uuid,
                            'last_activity_at': datetime.utcnow()
                        }
                    )
                    
                    remna_uuid = remna_user.uuid
                    source = "remna"
                    logger.info(f"Создан новый пользователь: telegram_id={telegram_id}, remna_uuid={remna_uuid}")
                else:
                    # Создаем пользователя только в локальной БД (без Remna)
                    telegram_user = await user_repo.upsert_user_by_telegram_id(
                        telegram_id=telegram_id,
                        defaults={
                            'username': f"tg_{telegram_id}",
                            'first_name': tg_name.split()[0] if tg_name else None,
                            'remna_user_id': None,  # Нет привязки к Remna
                            'last_activity_at': datetime.utcnow()
                        }
                    )
                    
                    remna_uuid = None
                    source = "local_db"
                    logger.info(f"Создан новый пользователь только в локальной БД: telegram_id={telegram_id} (без Remna)")
                
                # Подписки нет (новый пользователь)
                # Не создаем подписку, т.к. в Remna её нет
                
                await session.commit()
                
                return SyncResult(
                    is_new_user_created=is_new_user_created,
                    user_remna_uuid=remna_uuid,
                    subscription_status="none",
                    expires_at=None,
                    source=source
                )
            
            except Exception as e:
                await session.rollback()
                import traceback
                logger.error(
                    f"Ошибка при создании пользователя {telegram_id} в БД: {e}\n"
                    f"Traceback: {traceback.format_exc()}"
                )
                raise
    
    async def _fallback_sync(self, telegram_id: int) -> SyncResult:
        """
        Fallback синхронизация из БД (read-only).
        Используется когда Remna недоступна.
        """
        async with SessionLocal() as session:
            user_repo = UserRepo(session)
            sub_repo = SubscriptionRepo(session)
            
            telegram_user = await user_repo.get_user_by_telegram_id(telegram_id)
            if not telegram_user:
                # Пользователя нет в БД - не можем использовать fallback
                raise RemnaUnavailableError("Пользователь не найден в БД, Remna недоступна")
            
            subscription = await sub_repo.get_subscription_by_user_id(telegram_id)
            
            subscription_status = "none"
            expires_at = None
            
            if subscription:
                if subscription.active and subscription.valid_until:
                    if subscription.valid_until > datetime.utcnow():
                        subscription_status = "active"
                        expires_at = subscription.valid_until
                    else:
                        subscription_status = "expired"
                        expires_at = subscription.valid_until
            
            logger.warning(
                f"Fallback синхронизация для {telegram_id}: "
                f"subscription_status={subscription_status} (данные могут быть устаревшими)"
            )
            
            return SyncResult(
                is_new_user_created=False,
                user_remna_uuid=telegram_user.remna_user_id,
                subscription_status=subscription_status,
                expires_at=expires_at,
                source="db_fallback"
            )
