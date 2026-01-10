from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import secrets
import string
from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotification
from app.config import settings
from app.logger import logger
from app.db.session import SessionLocal
from app.db.models import Payment as PaymentModel, Subscription, TelegramUser, RemnaUser
from sqlalchemy import select
from app.remnawave.client import RemnaClient

Configuration.account_id = settings.YOOKASSA_SHOP_ID
Configuration.secret_key = settings.YOOKASSA_API_KEY


async def get_squad_name_for_plan(plan_code: Optional[str]) -> Optional[str]:
    """Возвращает имя сквада для плана подписки"""
    if not plan_code:
        return None
    
    plan_code_lower = plan_code.lower()
    if plan_code_lower == 'basic':
        return 'basic'  # Исправлено: было 'base', должно быть 'basic'
    elif plan_code_lower == 'premium':
        return 'premium'
    elif plan_code_lower == 'trial':
        # Пробный период использует базовый сквад
        return 'basic'  # Исправлено: было 'base', должно быть 'basic'
    
    return None


def generate_remna_password(length: int = 24) -> str:
    """
    Генерирует безопасный пароль для Remna API
    
    Требования Remna API:
    - Минимум 24 символа
    - Должен содержать заглавные и строчные буквы
    - Должен содержать цифры
    """
    if length < 24:
        length = 24
    
    # Гарантируем наличие всех требуемых типов символов
    uppercase = secrets.choice(string.ascii_uppercase)
    lowercase = secrets.choice(string.ascii_lowercase)
    digits = secrets.choice(string.digits)
    
    # Генерируем остальные символы
    all_chars = string.ascii_letters + string.digits
    remaining = ''.join(secrets.choice(all_chars) for _ in range(length - 3))
    
    # Смешиваем все символы
    password_chars = list(uppercase + lowercase + digits + remaining)
    secrets.SystemRandom().shuffle(password_chars)
    
    return ''.join(password_chars)

async def create_payment(amount_rub: int, description: str, user_id: int) -> str:
    """Создает платеж в YooKassa и возвращает URL для оплаты"""
    try:
        if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_API_KEY:
            raise ValueError("YOOKASSA_SHOP_ID и YOOKASSA_API_KEY должны быть настроены")
        
        # Проверяем, что API ключ не пустой и имеет правильный формат
        api_key = str(settings.YOOKASSA_API_KEY).strip()
        if not api_key:
            raise ValueError("YOOKASSA_API_KEY пустой")
        
        # Убеждаемся, что Configuration правильно настроен
        shop_id = str(settings.YOOKASSA_SHOP_ID).strip()
        Configuration.account_id = shop_id
        Configuration.secret_key = api_key
        
        # Логируем для отладки (без полного ключа)
        logger.debug(f"YooKassa Configuration: account_id={shop_id}, secret_key length={len(api_key)}")
        
        # Проверяем return_url
        if not settings.YOOKASSA_RETURN_URL:
            raise ValueError("YOOKASSA_RETURN_URL должен быть настроен")
        
        payment_data = {
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": str(settings.YOOKASSA_RETURN_URL)},
            "description": description,
            "metadata": {"tg_user_id": user_id},
            # Добавляем поддержку SBP (Система быстрых платежей)
            "payment_method_types": ["bank_card", "sbp", "yoo_money"]
        }
        
        try:
            p = Payment.create(payment_data)
            payment_id = p.id
            payment_url = p.confirmation.confirmation_url
        except Exception as api_error:
            error_msg = str(api_error)
            # Проверяем специфичные ошибки YooKassa
            if "invalid_credentials" in error_msg.lower() or "password format" in error_msg.lower() or "unauthorized" in error_msg.lower():
                logger.error(f"Ошибка авторизации YooKassa: Проверьте правильность YOOKASSA_API_KEY (должен быть Secret key из Merchant Profile)")
                logger.error(f"Детали ошибки: {error_msg}")
                raise ValueError("Ошибка авторизации YooKassa: проверьте правильность API ключа (должен быть Secret key, а не test ключ)")
            elif "shop_id" in error_msg.lower() or "account_id" in error_msg.lower():
                logger.error(f"Ошибка YooKassa: Проверьте правильность YOOKASSA_SHOP_ID")
                logger.error(f"Детали ошибки: {error_msg}")
                raise ValueError("Ошибка YooKassa: проверьте правильность Shop ID")
            else:
                logger.error(f"Ошибка API YooKassa: {error_msg}")
                import traceback
                logger.debug(traceback.format_exc())
                raise
        
        # Сохраняем платеж в БД
        if SessionLocal:
            try:
                async with SessionLocal() as session:
                    # Проверяем, существует ли уже такой платеж
                    result = await session.execute(
                        select(PaymentModel).where(PaymentModel.external_id == payment_id)
                    )
                    existing_payment = result.scalar_one_or_none()
                    
                    if not existing_payment:
                        new_payment = PaymentModel(
                            telegram_user_id=user_id,
                            provider="yookassa",
                            external_id=payment_id,
                            amount=amount_rub,
                            currency="RUB",
                            status=p.status or "pending",
                            description=description,
                            payment_metadata={"payment_data": payment_data, "yookassa_response": p.dict() if hasattr(p, 'dict') else str(p)}
                        )
                        session.add(new_payment)
                        await session.commit()
                        logger.info(f"Платеж {payment_id} сохранен в БД для пользователя {user_id}")
                    else:
                        logger.warning(f"Платеж {payment_id} уже существует в БД")
            except Exception as e:
                logger.error(f"Ошибка при сохранении платежа в БД: {e}")
                # Не прерываем процесс, платеж уже создан в Yookassa
        
        logger.info(f"Создан платеж {payment_id} для пользователя {user_id}, сумма: {amount_rub}₽")
        return payment_url
        
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        raise


async def process_payment_webhook(webhook_data: Dict[str, Any], bot) -> bool:
    """Обрабатывает webhook от YooKassa"""
    try:
        # Валидация webhook данных
        if not webhook_data:
            logger.error("Получен пустой webhook")
            return False
        
        event = webhook_data.get("event")
        if not event:
            logger.error("Webhook не содержит поле 'event'")
            return False
        
        logger.info(f"Получен webhook событие: {event}")
        
        # Парсим уведомление
        notification = WebhookNotification(webhook_data)
        payment = notification.object
        
        if not payment:
            logger.error("Webhook не содержит объект платежа")
            return False
        
        payment_id = payment.id
        status = payment.status
        amount = float(payment.amount.value)
        currency = payment.amount.currency
        description = payment.description
        metadata = payment.metadata or {}
        telegram_user_id = metadata.get("tg_user_id")
        
        if not telegram_user_id:
            logger.error(f"Платеж {payment_id} не содержит telegram_user_id в metadata")
            return False
        
        telegram_user_id = int(telegram_user_id)
        
        # Убеждаемся, что пользователь существует в БД
        from app.services.users import get_or_create_telegram_user
        try:
            await get_or_create_telegram_user(
                telegram_id=telegram_user_id,
                username=None,
                first_name=None,
                last_name=None,
                language_code=None,
                create_trial=False  # Не создаем пробную подписку при webhook
            )
        except Exception as e:
            logger.warning(f"Не удалось создать/получить пользователя {telegram_user_id} при webhook: {e}")
        
        logger.info(f"Получен webhook для платежа {payment_id}, статус: {status}, пользователь: {telegram_user_id}, сумма: {amount} {currency}")
        
        if not SessionLocal:
            logger.error("БД не настроена, платеж не может быть обработан")
            return False
        
        async with SessionLocal() as session:
            result = await session.execute(
                select(PaymentModel).where(PaymentModel.external_id == payment_id)
            )
            existing_payment = result.scalar_one_or_none()
            
            payment_db = None
            if existing_payment:
                # Обновляем существующий платеж
                old_status = existing_payment.status
                existing_payment.status = status
                existing_payment.updated_at = datetime.utcnow()
                existing_payment.amount = amount
                if status == "succeeded" and not existing_payment.paid_at:
                    existing_payment.paid_at = datetime.utcnow()
                existing_payment.payment_metadata = webhook_data
                await session.commit()
                payment_db = existing_payment
                logger.info(f"Обновлен платеж {payment_id}, статус изменен: {old_status} -> {status}")
            else:
                # Создаем новый платеж (если webhook пришел раньше, чем платеж был сохранен при создании)
                # Убеждаемся, что пользователь существует в БД
                from app.services.users import get_or_create_telegram_user
                try:
                    await get_or_create_telegram_user(
                        telegram_id=telegram_user_id,
                        username=None,
                        first_name=None,
                        last_name=None,
                        language_code=None,
                        create_trial=False  # Не создаем пробную подписку при webhook
                    )
                except Exception as e:
                    logger.warning(f"Не удалось создать/получить пользователя {telegram_user_id} при webhook: {e}")
                
                new_payment = PaymentModel(
                    telegram_user_id=telegram_user_id,
                    provider="yookassa",
                    external_id=payment_id,
                    amount=amount,
                    currency=currency,
                    status=status,
                    description=description,
                    payment_metadata=webhook_data
                )
                if status == "succeeded":
                    new_payment.paid_at = datetime.utcnow()
                session.add(new_payment)
                await session.commit()
                await session.refresh(new_payment)
                payment_db = new_payment
                logger.info(f"Создан платеж {payment_id} из webhook, статус: {status}")
            
            # Обрабатываем успешный платеж только если статус изменился на succeeded
            if status == "succeeded" and payment_db:
                # Проверяем, не обработан ли уже этот платеж
                if not payment_db.subscription_id:
                    await handle_successful_payment(
                        session=session,
                        payment_id=payment_db.id,
                        telegram_user_id=telegram_user_id,
                        amount=amount,
                        description=description or "CRS VPN 30 дней",
                        bot=bot
                    )
                else:
                    logger.info(f"Платеж {payment_id} уже обработан, подписка ID: {payment_db.subscription_id}")
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при обработке webhook платежа: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


async def handle_successful_payment(
    session,
    payment_id: int,
    telegram_user_id: int,
    amount: float,
    description: str,
    bot
) -> None:
    # Инвалидируем кэш подписки и синхронизации при успешном платеже
    try:
        from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache
        await invalidate_subscription_cache(telegram_user_id)
        await invalidate_sync_cache(telegram_user_id)
        logger.debug(f"Кэш инвалидирован для пользователя {telegram_user_id} после успешного платежа")
    except Exception as e:
        logger.debug(f"Ошибка инвалидации кэша: {e}")
    """Обрабатывает успешный платеж создает подписку и отправляет пользователю ссылку"""
    try:
        # Сначала пытаемся получить тариф и период из payment_metadata
        plan_code = None
        period_months = None
        
        payment_result = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id)
        )
        payment = payment_result.scalar_one_or_none()
        
        if payment and payment.payment_metadata:
            metadata = payment.payment_metadata
            if isinstance(metadata, dict):
                plan_code = metadata.get("plan_code")
                period_months = metadata.get("period_months")
                if period_months:
                    try:
                        period_months = int(period_months)
                    except (ValueError, TypeError):
                        period_months = None
        
        # Если не нашли в metadata, определяем тариф и период по сумме платежа
        if not plan_code or not period_months:
            # Базовый: 99 (1 мес), 249 (3 мес), 499 (6 мес), 899 (12 мес)
            # Премиум: 199 (1 мес), 549 (3 мес), 999 (6 мес), 1799 (12 мес)
            # Проверяем от больших сумм к меньшим
            if amount >= 1799:
                plan_code = "premium"
                period_months = 12
            elif amount >= 999:
                plan_code = "premium"
                period_months = 6
            elif amount >= 549:
                plan_code = "premium"
                period_months = 3
            elif amount >= 199:
                plan_code = "premium"
                period_months = 1
            elif amount >= 899:
                plan_code = "basic"
                period_months = 12
            elif amount >= 499:
                plan_code = "basic"
                period_months = 6
            elif amount >= 249:
                plan_code = "basic"
                period_months = 3
            elif amount >= 99:
                plan_code = "basic"
                period_months = 1
            else:
                # По умолчанию базовый на месяц
                plan_code = "basic"
                period_months = 1
        
        # Определяем plan_name и period_days
        if plan_code == "premium":
            plan_name = "Премиум"
        else:
            plan_name = "Базовый"
        
        period_days = period_months * 30  # Примерно 30 дней в месяце
        valid_until = datetime.utcnow() + timedelta(days=period_days)
        
        user_result = await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
        )
        telegram_user = user_result.scalar_one_or_none()
        
        if not telegram_user:
            logger.error(f"Пользователь {telegram_user_id} не найден в БД")
            return
        
        sub_result = await session.execute(
            select(Subscription).where(
                Subscription.telegram_user_id == telegram_user_id,
                Subscription.active == True
            )
        )
        existing_sub = sub_result.scalar_one_or_none()
        
        if existing_sub:
            existing_sub.plan_code = plan_code
            existing_sub.plan_name = plan_name
            existing_sub.valid_until = valid_until
            existing_sub.active = True
            subscription = existing_sub
            logger.info(f"Обновлена подписка для пользователя {telegram_user_id}")
        else:
            subscription = Subscription(
                telegram_user_id=telegram_user_id,
                plan_code=plan_code,
                plan_name=plan_name,
                active=True,
                valid_until=valid_until
            )
            session.add(subscription)
            logger.info(f"Создана подписка для пользователя {telegram_user_id}")
        
        await session.commit()
        await session.refresh(subscription)
        
        payment_result = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id)
        )
        payment = payment_result.scalar_one_or_none()
        if payment:
            payment.subscription_id = subscription.id
            payment.status = "succeeded"
            payment.paid_at = datetime.utcnow()
            await session.commit()
        
        subscription_url = await get_or_create_remna_user_and_get_subscription_url(
            telegram_user_id=telegram_user_id,
            subscription_id=subscription.id
        )
        
        if subscription_url:
            if not subscription.config_data:
                subscription.config_data = {}
            subscription.config_data["subscription_url"] = subscription_url
            await session.commit()
        
        message_text = (
            "✅ <b>Платеж успешно обработан!</b>\n\n"
            f"💳 <b>Тариф:</b> {plan_name}\n"
            f"📅 <b>Действует до:</b> {valid_until.strftime('%d.%m.%Y %H:%M')}\n"
            f"💰 <b>Сумма:</b> {amount:.2f}₽\n\n"
            "🎉 Ваша подписка активирована! Теперь вы можете получить ссылку для настройки VPN."
        )
        
        from app.keyboards import get_subscription_link_keyboard
        await bot.send_message(
            chat_id=telegram_user_id,
            text=message_text,
            reply_markup=get_subscription_link_keyboard(),
            parse_mode="HTML"
        )
        
        logger.info(f"Пользователю {telegram_user_id} отправлено сообщение об успешной оплате")
        
    except Exception as e:
        logger.error(f"Ошибка при обработке успешного платежа: {e}")
        import traceback
        logger.debug(traceback.format_exc())


async def check_payment_status(payment_id: str) -> Optional[Dict[str, Any]]:
    """Проверяет статус платежа в YooKassa"""
    try:
        if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_API_KEY:
            logger.error("YOOKASSA_SHOP_ID и YOOKASSA_API_KEY должны быть настроены")
            return None
        
        payment = Payment.find_one(payment_id)
        
        if not payment:
            logger.error(f"Платеж {payment_id} не найден в YooKassa")
            return None
        
        return {
            "id": payment.id,
            "status": payment.status,
            "amount": float(payment.amount.value),
            "currency": payment.amount.currency,
            "description": payment.description,
            "metadata": payment.metadata or {},
            "paid": payment.paid,
            "created_at": payment.created_at.isoformat() if hasattr(payment, 'created_at') and payment.created_at else None,
            "captured_at": payment.captured_at.isoformat() if hasattr(payment, 'captured_at') and payment.captured_at else None,
        }
    except Exception as e:
        logger.error(f"Ошибка при проверке статуса платежа {payment_id}: {e}")
        return None


async def get_or_create_remna_user_and_get_subscription_url(
    telegram_user_id: int,
    subscription_id: int
) -> Optional[str]:
    """Получает или создает пользователя в Remna API и возвращает subscription URL"""
    try:
        if not SessionLocal:
            return None
        
        async with SessionLocal() as session:
            user_result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
            )
            telegram_user = user_result.scalar_one_or_none()
            
            if not telegram_user:
                logger.error(f"Пользователь {telegram_user_id} не найден")
                return None
            
            sub_result = await session.execute(
                select(Subscription).where(Subscription.id == subscription_id)
            )
            subscription = sub_result.scalar_one_or_none()
            
            if not subscription:
                logger.error(f"Подписка {subscription_id} не найдена")
                return None
            
            client = RemnaClient()
            
            try:
                if telegram_user.remna_user_id:
                    # Если пользователь уже существует, обновляем сквад согласно текущей подписке
                    squad_name = await get_squad_name_for_plan(subscription.plan_code)
                    if squad_name:
                        try:
                            squad = await client.get_squad_by_name(squad_name)
                            if squad:
                                squad_uuid = squad.get('uuid')
                                logger.info(f"📝 Обновляю сквад для существующего пользователя {telegram_user.remna_user_id}: {squad_name}")
                                try:
                                    await client.update_user(str(telegram_user.remna_user_id), activeInternalSquads=[squad_uuid])
                                    logger.info(f"✅ Сквад обновлен в Remna для пользователя {telegram_user.remna_user_id}")
                                except Exception as squad_update_e:
                                    logger.warning(f"⚠️ Не удалось обновить сквад для пользователя {telegram_user.remna_user_id}: {squad_update_e}")
                        except Exception as squad_e:
                            logger.warning(f"⚠️ Ошибка при получении сквада {squad_name}: {squad_e}")
                    
                    subscription_url = await client.get_user_subscription_url(telegram_user.remna_user_id)
                    if subscription_url:
                        # Закрываем клиент перед возвратом
                        await client.close()
                        return subscription_url
                
                username = telegram_user.username or f"user_{telegram_user_id}"
                # Генерируем пароль, соответствующий требованиям Remna API
                password = generate_remna_password(length=24)
                
                # Получаем дату истечения из подписки (Remna API требует expireAt)
                expire_at = None
                if subscription.valid_until:
                    # Форматируем дату в ISO 8601 формат для Remna API
                    expire_at = subscription.valid_until.isoformat()
                
                # Получаем сквад для плана подписки перед созданием пользователя
                squad_name = await get_squad_name_for_plan(subscription.plan_code)
                squad_uuid = None
                if squad_name:
                    try:
                        squad = await client.get_squad_by_name(squad_name)
                        if squad:
                            squad_uuid = squad.get('uuid')
                            logger.info(f"📝 Найден сквад {squad_name} (UUID: {squad_uuid}) для назначения при создании")
                    except Exception as squad_e:
                        logger.warning(f"⚠️ Ошибка при получении сквада {squad_name}: {squad_e}")
                
                logger.info(f"Создание пользователя в Remna API: username={username}, telegram_id={telegram_user_id}, expire_at={expire_at}")
                try:
                    create_payload = {
                        "username": username,
                        "password": password,
                    }
                    if expire_at:
                        create_payload["expireAt"] = expire_at
                    if telegram_user_id:
                        create_payload["telegramId"] = int(telegram_user_id)
                    if squad_uuid:
                        create_payload["activeInternalSquads"] = [squad_uuid]
                        logger.info(f"📝 Добавляю сквад {squad_name} при создании пользователя")
                    
                    remna_user_data = await client.create_user(
                        username=username, 
                        password=password, 
                        expire_at=expire_at,
                        telegram_id=telegram_user_id,
                        active_internal_squads=[squad_uuid] if squad_uuid else None
                    )
                    logger.info(f"Ответ Remna API при создании пользователя: {remna_user_data}")
                except Exception as e:
                    error_msg = str(e)
                    # Пробуем извлечь текст ошибки из response, если это HTTPStatusError
                    error_text = error_msg
                    error_json = None
                    
                    # Проверяем, есть ли response с текстом ошибки (для httpx.HTTPStatusError)
                    import httpx
                    if isinstance(e, httpx.HTTPStatusError):
                        try:
                            if hasattr(e, 'response') and e.response:
                                if hasattr(e.response, 'text') and e.response.text:
                                    error_text = e.response.text
                                    try:
                                        import json
                                        error_json = json.loads(e.response.text)
                                        if isinstance(error_json, dict):
                                            error_text = error_json.get('message', error_text)
                                            logger.warning(f"⚠️ Ошибка при создании пользователя (из JSON): {error_text}")
                                        else:
                                            logger.warning(f"⚠️ Ошибка при создании пользователя (из response.text): {error_text}")
                                    except:
                                        logger.warning(f"⚠️ Ошибка при создании пользователя (из response.text): {error_text}")
                        except Exception as parse_e:
                            logger.warning(f"⚠️ Не удалось извлечь текст ошибки: {parse_e}")
                    
                    if not error_json:
                        logger.warning(f"⚠️ Ошибка при создании пользователя: {error_msg}")
                    
                    # Если пользователь уже существует, получаем его из API
                    # Проверяем все возможные варианты ошибки
                    is_already_exists = (
                        "already exists" in error_msg.lower() or 
                        "already exists" in error_text.lower() or
                        "A019" in error_msg or 
                        "A019" in error_text or
                        (error_json and error_json.get('errorCode') == 'A019') or
                        "User username already exists" in error_msg or
                        "User username already exists" in error_text or
                        "username already exists" in error_msg.lower() or
                        "username already exists" in error_text.lower()
                    )
                    if is_already_exists:
                        logger.info(f"✅ Обнаружена ошибка 'already exists', ищем пользователя в Remna API...")
                        logger.info(f"   error_msg: {error_msg[:100]}")
                        logger.info(f"   error_text: {error_text[:100]}")
                        if error_json:
                            logger.info(f"   error_json: {error_json}")
                        logger.warning(f"⚠️ Пользователь {username} уже существует в Remna API, получаю его данные...")
                        # Ищем пользователя по telegram_id (приоритет) или username через API
                        try:
                            users_response = await client.get_users(size=100, start=1)
                            response_data = users_response.get('response', {})
                            users = response_data.get('users', [])
                            
                            found_user = None
                            # Сначала ищем по telegram_id (более надежно)
                            for user_data in users:
                                if user_data.get('telegramId') == telegram_user_id:
                                    found_user = user_data
                                    logger.info(f"✅ Найден пользователь по telegramId={telegram_user_id}")
                                    break
                            
                            # Если не нашли по telegram_id, ищем по username
                            if not found_user:
                                for user_data in users:
                                    if user_data.get('username') == username:
                                        found_user = user_data
                                        logger.info(f"✅ Найден пользователь по username={username}")
                                        break
                            
                            # Если не нашли на первой странице, ищем дальше
                            if not found_user:
                                page_size = 50
                                start = 51
                                while True:
                                    users_response = await client.get_users(size=page_size, start=start)
                                    response_data = users_response.get('response', {})
                                    users = response_data.get('users', [])
                                    
                                    if not users:
                                        break
                                    
                                    # Сначала по telegram_id
                                    for user_data in users:
                                        if user_data.get('telegramId') == telegram_user_id:
                                            found_user = user_data
                                            logger.info(f"✅ Найден пользователь по telegramId={telegram_user_id} (страница {start//page_size + 1})")
                                            break
                                    
                                    # Если не нашли, по username
                                    if not found_user:
                                        for user_data in users:
                                            if user_data.get('username') == username:
                                                found_user = user_data
                                                logger.info(f"✅ Найден пользователь по username={username} (страница {start//page_size + 1})")
                                                break
                                    
                                    if found_user or len(users) < page_size:
                                        break
                                    
                                    start += page_size
                            
                            if found_user:
                                user_uuid = found_user.get('uuid')
                                if user_uuid:
                                    logger.info(f"✅ Найден существующий пользователь: {user_uuid}")
                                    
                                    # Обновляем expireAt в Remna для существующего пользователя
                                    if subscription.valid_until:
                                        expire_at = subscription.valid_until.isoformat()
                                        logger.info(f"📝 Обновляю expireAt для пользователя {user_uuid}: {expire_at}")
                                        try:
                                            await client.update_user(str(user_uuid), expireAt=expire_at)
                                            logger.info(f"✅ expireAt обновлен в Remna")
                                        except Exception as update_e:
                                            logger.warning(f"⚠️ Не удалось обновить expireAt: {update_e}")
                                    
                                    # Обновляем telegramId если его нет
                                    if not found_user.get('telegramId'):
                                        logger.info(f"📝 Обновляю telegramId для пользователя {user_uuid}")
                                        try:
                                            await client.update_user(str(user_uuid), telegramId=int(telegram_user_id))
                                            logger.info(f"✅ telegramId обновлен")
                                        except Exception as update_e:
                                            logger.warning(f"⚠️ Не удалось обновить telegramId: {update_e}")
                                    
                                    # Обновляем сквад если нужно
                                    squad_name = await get_squad_name_for_plan(subscription.plan_code)
                                    if squad_name:
                                        try:
                                            squad = await client.get_squad_by_name(squad_name)
                                            if squad:
                                                squad_uuid = squad.get('uuid')
                                                logger.info(f"📝 Обновляю сквад для пользователя {user_uuid}: {squad_name}")
                                                try:
                                                    await client.update_user(str(user_uuid), activeInternalSquads=[squad_uuid])
                                                    logger.info(f"✅ Сквад обновлен в Remna")
                                                except Exception as squad_update_e:
                                                    logger.warning(f"⚠️ Не удалось обновить сквад: {squad_update_e}")
                                        except Exception as squad_e:
                                            logger.warning(f"⚠️ Ошибка при получении сквада {squad_name}: {squad_e}")
                                    
                                    # Получаем полные данные пользователя
                                    remna_user_data = await client.get_user_by_id(str(user_uuid))
                                    logger.info(f"✅ Получены данные существующего пользователя")
                                    logger.info(f"📋 Структура данных пользователя: {list(remna_user_data.keys()) if isinstance(remna_user_data, dict) else type(remna_user_data)}")
                                    if isinstance(remna_user_data, dict):
                                        logger.info(f"📋 Ключи в response: {list(remna_user_data.get('response', {}).keys()) if isinstance(remna_user_data.get('response'), dict) else 'response не dict'}")
                                    
                                    # Пытаемся извлечь subscription URL напрямую из данных пользователя
                                    subscription_url = None
                                    if isinstance(remna_user_data, dict):
                                        # Проверяем разные варианты структуры
                                        subscription_url = (
                                            remna_user_data.get('subscriptionUrl') or 
                                            remna_user_data.get('subscription_url') or
                                            (remna_user_data.get('response', {}) or {}).get('subscriptionUrl') or
                                            (remna_user_data.get('response', {}) or {}).get('subscription_url')
                                        )
                                        
                                        # Если нашли token, формируем URL
                                        if not subscription_url:
                                            subscription_token = (
                                                remna_user_data.get('subscriptionToken') or 
                                                remna_user_data.get('subscription_token') or
                                                (remna_user_data.get('response', {}) or {}).get('subscriptionToken') or
                                                (remna_user_data.get('response', {}) or {}).get('subscription_token')
                                            )
                                            if subscription_token:
                                                subscription_url = f"https://sub.crs-projects.com/{subscription_token}"
                                                logger.info(f"✅ Сформирован subscription URL из token: {subscription_url[:50]}...")
                                    
                                    # Если не нашли в данных, используем метод клиента
                                    if not subscription_url:
                                        subscription_url = await client.get_user_subscription_url(str(user_uuid))
                                    
                                    if subscription_url and subscription_url.strip():
                                        subscription_url = subscription_url.strip()
                                        logger.info(f"✅ Получен subscription URL для существующего пользователя: {subscription_url[:50]}...")
                                        
                                        # Сохраняем remna_user_id и subscription_url в БД
                                        from app.db.models import RemnaUser
                                        remna_user_result = await session.execute(
                                            select(RemnaUser).where(RemnaUser.remna_id == str(user_uuid))
                                        )
                                        remna_user = remna_user_result.scalar_one_or_none()
                                        
                                        if not remna_user:
                                            remna_user = RemnaUser(
                                                remna_id=str(user_uuid),
                                                username=username,
                                                raw_data=remna_user_data if isinstance(remna_user_data, dict) else {}
                                            )
                                            session.add(remna_user)
                                        
                                        telegram_user.remna_user_id = str(user_uuid)
                                        subscription.remna_user_id = str(user_uuid)
                                        
                                        if not subscription.config_data:
                                            subscription.config_data = {}
                                        subscription.config_data["subscription_url"] = subscription_url
                                        
                                        await session.commit()
                                        logger.info(f"✅ Данные сохранены в БД для существующего пользователя")
                                        
                                        await client.close()
                                        return subscription_url
                                    else:
                                        logger.warning(f"⚠️ Не удалось получить subscription URL для существующего пользователя {user_uuid}")
                                        logger.debug(f"Данные пользователя: {remna_user_data}")
                                        # Обновляем remna_user_id в БД даже если URL не получен
                                        telegram_user.remna_user_id = str(user_uuid)
                                        subscription.remna_user_id = str(user_uuid)
                                        await session.commit()
                                        await client.close()
                                        return None
                                else:
                                    logger.error(f"❌ Не удалось получить UUID существующего пользователя")
                                    return None
                            else:
                                logger.error(f"❌ Пользователь {username} не найден в API после ошибки 'already exists'")
                                return None
                        except Exception as search_e:
                            logger.error(f"❌ Ошибка при поиске существующего пользователя: {search_e}")
                            return None
                    else:
                        logger.error(f"Ошибка при создании пользователя в Remna API: {e}")
                        import traceback
                        logger.debug(traceback.format_exc())
                        return None
                
                remna_user_id = None
                if isinstance(remna_user_data, dict):
                    # Пробуем разные варианты получения UUID
                    # Структура ответа от /api/users может быть разной
                    remna_user_id = (
                        remna_user_data.get("uuid") or 
                        remna_user_data.get("id") or 
                        remna_user_data.get("_id") or
                        remna_user_data.get("userId")
                    )
                    
                    # Если не нашли в корне, проверяем вложенные структуры
                    if not remna_user_id:
                        if "response" in remna_user_data:
                            response = remna_user_data["response"]
                            # Может быть объект пользователя или список
                            if isinstance(response, dict):
                                remna_user_id = (
                                    response.get("uuid") or 
                                    response.get("id") or 
                                    response.get("_id") or
                                    response.get("userId")
                                )
                            elif isinstance(response, list) and len(response) > 0:
                                # Если ответ - список, берем первый элемент
                                first_user = response[0]
                                remna_user_id = (
                                    first_user.get("uuid") or 
                                    first_user.get("id") or 
                                    first_user.get("_id") or
                                    first_user.get("userId")
                                )
                        if not remna_user_id and "data" in remna_user_data:
                            data = remna_user_data["data"]
                            remna_user_id = (
                                data.get("uuid") or 
                                data.get("id") or 
                                data.get("_id") or
                                data.get("userId")
                            )
                
                if not remna_user_id:
                    logger.error(f"Не удалось получить ID пользователя из Remna API. Ответ: {remna_user_data}")
                    return None
                
                logger.info(f"Пользователь создан в Remna API: remna_user_id={remna_user_id}")
                
                # Извлекаем полные данные пользователя из ответа
                user_response_data = None
                if isinstance(remna_user_data, dict):
                    if "response" in remna_user_data and isinstance(remna_user_data["response"], dict):
                        user_response_data = remna_user_data["response"]
                    else:
                        user_response_data = remna_user_data
                
                # Получаем subscription URL из ответа (он уже есть в response)
                subscription_url = None
                if user_response_data:
                    subscription_url = (
                        user_response_data.get("subscriptionUrl") or 
                        user_response_data.get("subscription_url")
                    )
                    if not subscription_url and "subscriptionToken" in user_response_data:
                        token = user_response_data.get("subscriptionToken") or user_response_data.get("subscription_token")
                        if token:
                            subscription_url = f"https://sub.crs-projects.com/{token}"
                
                # Создаем запись в remna_users перед обновлением telegram_users (для Foreign Key)
                from app.db.models import RemnaUser
                remna_user_result = await session.execute(
                    select(RemnaUser).where(RemnaUser.remna_id == str(remna_user_id))
                )
                remna_user = remna_user_result.scalar_one_or_none()
                
                if not remna_user:
                    # Создаем новую запись в remna_users
                    remna_user = RemnaUser(
                        remna_id=str(remna_user_id),
                        username=username,
                        email=user_response_data.get("email") if user_response_data else None,
                        raw_data=user_response_data if user_response_data else remna_user_data
                    )
                    session.add(remna_user)
                    logger.info(f"Создана запись в remna_users для remna_id={remna_user_id}")
                else:
                    # Обновляем существующую запись
                    remna_user.username = username
                    remna_user.raw_data = user_response_data if user_response_data else remna_user_data
                    remna_user.last_synced_at = datetime.utcnow()
                    logger.info(f"Обновлена запись в remna_users для remna_id={remna_user_id}")
                
                # Теперь обновляем telegram_user и subscription
                telegram_user.remna_user_id = str(remna_user_id)
                subscription.remna_user_id = str(remna_user_id)
                
                # Сохраняем subscription URL в config_data подписки
                if subscription_url and subscription_url.strip():
                    if not subscription.config_data:
                        subscription.config_data = {}
                    subscription.config_data["subscription_url"] = subscription_url.strip()
                    logger.info(f"✅ Subscription URL сохранен в config_data: {subscription_url[:50]}...")
                else:
                    logger.warning(f"⚠️ Subscription URL не найден в ответе создания пользователя")
                
                await session.commit()
                
                # Если subscription URL не был в ответе, пытаемся получить его отдельным запросом
                if not subscription_url or not subscription_url.strip():
                    logger.info(f"📥 Получение subscription URL отдельным запросом для remna_user_id={remna_user_id}")
                    subscription_url = await client.get_user_subscription_url(str(remna_user_id))
                    if subscription_url and subscription_url.strip():
                        # Обновляем config_data с полученной ссылкой
                        if not subscription.config_data:
                            subscription.config_data = {}
                        subscription.config_data["subscription_url"] = subscription_url.strip()
                        await session.commit()
                        logger.info(f"✅ Subscription URL получен и сохранен: {subscription_url[:50]}...")
                    else:
                        logger.error(f"❌ Не удалось получить subscription URL для remna_user_id={remna_user_id}")
                
                # Обновляем пользователя для добавления Telegram ID (если его не было в ответе)
                # expireAt и настройки internal squads
                try:
                    update_payload = {}
                    
                    # Обновляем expireAt в Remna для нового пользователя
                    if subscription.valid_until:
                        expire_at = subscription.valid_until.isoformat()
                        update_payload["expireAt"] = expire_at
                        logger.info(f"📝 Обновляю expireAt для нового пользователя: {expire_at}")
                    
                    # Убеждаемся, что telegramId установлен
                    if user_response_data and not user_response_data.get("telegramId"):
                        update_payload["telegramId"] = int(telegram_user_id)  # API ожидает число
                    
                    # Добавляем internal squads в зависимости от плана подписки
                    squad_name = await get_squad_name_for_plan(subscription.plan_code)
                    if squad_name:
                        try:
                            squad = await client.get_squad_by_name(squad_name)
                            if squad:
                                squad_uuid = squad.get('uuid')
                                if squad_uuid:
                                    # Получаем текущие сквады пользователя
                                    # Если user_response_data еще не содержит актуальные данные, получаем их заново
                                    if not user_response_data or 'activeInternalSquads' not in user_response_data:
                                        user_data = await client.get_user_by_id(str(remna_user_id))
                                        if isinstance(user_data, dict):
                                            if "response" in user_data and isinstance(user_data["response"], dict):
                                                user_response_data = user_data["response"]
                                            else:
                                                user_response_data = user_data
                                    
                                    current_squads = user_response_data.get('activeInternalSquads', []) if user_response_data else []
                                    current_squad_uuids = []
                                    for s in current_squads:
                                        if isinstance(s, dict):
                                            current_squad_uuids.append(s.get('uuid'))
                                        elif isinstance(s, str):
                                            current_squad_uuids.append(s)
                                    
                                    # Если нужного сквада нет, добавляем его
                                    if squad_uuid not in current_squad_uuids:
                                        current_squad_uuids.append(squad_uuid)
                                        update_payload["activeInternalSquads"] = current_squad_uuids
                                        logger.info(f"📝 Добавлен сквад {squad_name} ({squad_uuid}) для плана {subscription.plan_code}")
                                    else:
                                        logger.info(f"✅ Сквад {squad_name} уже назначен пользователю")
                            else:
                                logger.warning(f"⚠️ Сквад {squad_name} не найден в Remnawave")
                        except Exception as squad_e:
                            logger.warning(f"⚠️ Ошибка при получении сквада {squad_name}: {squad_e}")
                    
                    if update_payload:
                        logger.info(f"Обновление пользователя в Remna API: {update_payload}")
                        await client.update_user(str(remna_user_id), **update_payload)
                        logger.info(f"Пользователь обновлен в Remna API")
                except Exception as e:
                    logger.warning(f"Не удалось обновить пользователя в Remna API (не критично): {e}")
                
                return subscription_url
                
            finally:
                await client.close()
                
    except Exception as e:
        logger.error(f"Ошибка при получении subscription URL: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return None