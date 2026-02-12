"""
Сервис для работы с криптовалютными платежами (USDT TRC20)
"""
from typing import Optional, Tuple
from datetime import datetime
from app.config import settings
from app.logger import logger
from app.db.session import SessionLocal
from app.db.models import Payment as PaymentModel, Subscription, TelegramUser
from sqlalchemy import select
from app.remnawave.client import RemnaClient


async def create_crypto_payment(amount_rub: int, description: str, user_id: int) -> Tuple[str, None]:
    """
    Создает крипто-платеж и возвращает адрес
    
    Returns:
        Tuple[address, None]
    """
    try:
        if not settings.CRYPTO_USDT_TRC20_ADDRESS:
            raise ValueError("CRYPTO_USDT_TRC20_ADDRESS не настроен в конфигурации")
        
        address = settings.CRYPTO_USDT_TRC20_ADDRESS
        
        # Создаем запись о платеже в БД
        if SessionLocal:
            try:
                async with SessionLocal() as session:
                    # Создаем уникальный ID для платежа (укорачиваем для совместимости)
                    timestamp = int(datetime.utcnow().timestamp())
                    # Используем более короткий формат: crypto_{user_id}_{timestamp}
                    # Максимальная длина: 7 + 10 + 1 + 10 = 28 символов (в пределах VARCHAR(128))
                    payment_id = f"crypto_{user_id}_{timestamp}"
                    
                    # Проверяем, существует ли уже такой платеж
                    result = await session.execute(
                        select(PaymentModel).where(PaymentModel.external_id == payment_id)
                    )
                    existing_payment = result.scalar_one_or_none()
                    
                    if not existing_payment:
                        try:
                            new_payment = PaymentModel(
                                telegram_user_id=user_id,
                                provider="crypto_usdt_trc20",
                                external_id=payment_id,
                                amount=amount_rub,
                                currency="RUB",
                                status="pending",
                                description=description,
                                payment_metadata={
                                    "crypto_address": address,
                                    "network": settings.CRYPTO_NETWORK or "TRC20",
                                    "amount_rub": amount_rub,
                                    "created_at": datetime.utcnow().isoformat()
                                }
                            )
                            session.add(new_payment)
                            await session.commit()
                            logger.info(f"Крипто-платеж {payment_id} сохранен в БД для пользователя {user_id}")
                        except Exception as db_error:
                            await session.rollback()
                            error_str = str(db_error)
                            if "StringDataRightTruncationError" in error_str or "value too long" in error_str.lower():
                                logger.error(f"Ошибка: external_id слишком длинный. Текущая длина: {len(payment_id)} символов. Нужно проверить миграцию БД.")
                            logger.error(f"Ошибка при сохранении крипто-платежа в БД: {db_error}")
                            raise
                    else:
                        logger.warning(f"Крипто-платеж {payment_id} уже существует в БД")
            except Exception as e:
                logger.error(f"Ошибка при сохранении крипто-платежа в БД: {e}")
        
        logger.info(f"Создан крипто-платеж для пользователя {user_id}, сумма: {amount_rub}₽, адрес: {address}")
        return address, None
        
    except Exception as e:
        logger.error(f"Ошибка при создании крипто-платежа: {e}")
        raise


async def confirm_crypto_payment(payment_id: str, transaction_hash: Optional[str] = None) -> bool:
    """
    Подтверждает крипто-платеж (вызывается вручную администратором или через webhook)
    
    Args:
        payment_id: ID платежа (external_id)
        transaction_hash: Хеш транзакции (опционально)
    
    Returns:
        bool: True если платеж подтвержден
    """
    try:
        if not SessionLocal:
            logger.error("БД не настроена")
            return False
        
        async with SessionLocal() as session:
            result = await session.execute(
                select(PaymentModel).where(PaymentModel.external_id == payment_id)
            )
            payment = result.scalar_one_or_none()
            
            if not payment:
                logger.error(f"Платеж {payment_id} не найден")
                return False
            
            if payment.status == "succeeded":
                logger.warning(f"Платеж {payment_id} уже подтвержден")
                return True
            
            # Обновляем статус платежа
            payment.status = "succeeded"
            payment.paid_at = datetime.utcnow()
            if transaction_hash:
                if not payment.payment_metadata:
                    payment.payment_metadata = {}
                payment.payment_metadata["transaction_hash"] = transaction_hash
            payment.updated_at = datetime.utcnow()
            
            await session.commit()
            logger.info(f"Крипто-платеж {payment_id} подтвержден")
            
            return True
            
    except Exception as e:
        logger.error(f"Ошибка при подтверждении крипто-платежа: {e}")
        return False


async def get_pending_crypto_payments(user_id: Optional[int] = None) -> list:
    """
    Получает список ожидающих крипто-платежей
    
    Args:
        user_id: ID пользователя (опционально, если None - все платежи)
    
    Returns:
        list: Список платежей
    """
    try:
        if not SessionLocal:
            return []
        
        async with SessionLocal() as session:
            query = select(PaymentModel).where(
                PaymentModel.provider == "crypto_usdt_trc20",
                PaymentModel.status == "pending"
            )
            
            if user_id:
                query = query.where(PaymentModel.telegram_user_id == user_id)
            
            result = await session.execute(query)
            payments = result.scalars().all()
            
            return [
                {
                    "id": p.id,
                    "external_id": p.external_id,
                    "user_id": p.telegram_user_id,
                    "amount": float(p.amount),
                    "currency": p.currency,
                    "description": p.description,
                    "created_at": p.created_at,
                    "metadata": p.payment_metadata
                }
                for p in payments
            ]
            
    except Exception as e:
        logger.error(f"Ошибка при получении списка крипто-платежей: {e}")
        return []


def get_usdt_amount(plan_code: str, period_months: int) -> float:
    """
    Возвращает фиксированную сумму в USDT для тарифа и периода
    
    Args:
        plan_code: Код тарифа (basic или premium)
        period_months: Период в месяцах (1, 3, 6, 12)
    
    Returns:
        float: Сумма в USDT
    """
    # Фиксированные цены в USDT
    basic_prices = {
        1: 1.49,
        3: 3.49,
        6: 5.99,
        12: 10.99
    }
    
    premium_prices = {
        1: 2.99,
        3: 6.99,
        6: 11.99,
        12: 21.99
    }
    
    if plan_code == "basic":
        return basic_prices.get(period_months, 1.49)
    elif plan_code == "premium":
        return premium_prices.get(period_months, 2.99)
    else:
        return 1.49  # По умолчанию базовый 1 месяц

def calculate_usdt_amount(rub_amount: int) -> float:
    """
    Рассчитывает сумму в USDT (примерный курс, можно заменить на реальный API)
    Используется для обратной совместимости
    
    Args:
        rub_amount: Сумма в рублях
    
    Returns:
        float: Сумма в USDT
    """
    # Примерный курс 1 USDT = 100 RUB (нужно заменить на реальный курс)
    # Можно использовать API для получения актуального курса
    usdt_rate = 100.0  # TODO: Получать из API
    return round(rub_amount / usdt_rate, 2)

