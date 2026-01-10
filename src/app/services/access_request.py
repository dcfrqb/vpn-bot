"""Сервис для работы с запросами на выдачу VPN-доступа"""
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccessRequest, TelegramUser
from app.db.session import SessionLocal
from app.logger import logger


async def create_access_request(
    telegram_id: int,
    name: Optional[str] = None,
    username: Optional[str] = None
) -> Optional[AccessRequest]:
    """
    Создает новый запрос на доступ
    
    Args:
        telegram_id: Telegram ID пользователя
        name: Имя пользователя
        username: Username пользователя
        
    Returns:
        AccessRequest или None в случае ошибки
    """
    if not SessionLocal:
        logger.error("БД не настроена, запрос не может быть создан")
        return None
    
    async with SessionLocal() as session:
        try:
            access_request = AccessRequest(
                telegram_id=telegram_id,
                name=name,
                username=username,
                status="pending"
            )
            session.add(access_request)
            await session.commit()
            await session.refresh(access_request)
            
            logger.info(f"Создан запрос на доступ {access_request.id} для пользователя {telegram_id}")
            return access_request
            
        except Exception as e:
            logger.error(f"Ошибка при создании запроса на доступ для пользователя {telegram_id}: {e}")
            await session.rollback()
            return None


async def has_pending_request(telegram_id: int) -> bool:
    """
    Проверяет, есть ли у пользователя активный pending-запрос
    
    Args:
        telegram_id: Telegram ID пользователя
        
    Returns:
        True если есть pending-запрос, False иначе
    """
    if not SessionLocal:
        return False
    
    async with SessionLocal() as session:
        try:
            result = await session.execute(
                select(AccessRequest).where(
                    and_(
                        AccessRequest.telegram_id == telegram_id,
                        AccessRequest.status == "pending"
                    )
                )
            )
            request = result.scalar_one_or_none()
            return request is not None
            
        except Exception as e:
            logger.error(f"Ошибка при проверке pending-запроса для пользователя {telegram_id}: {e}")
            return False


async def can_create_request(telegram_id: int) -> Tuple[bool, Optional[str]]:
    """
    Проверяет, может ли пользователь создать запрос (rate-limit: 1 запрос / 1 час)
    
    Args:
        telegram_id: Telegram ID пользователя
        
    Returns:
        Tuple[bool, Optional[str]]: (может создать, сообщение об ошибке если нельзя)
    """
    if not SessionLocal:
        return False, "БД не настроена"
    
    # Проверяем pending-запрос
    if await has_pending_request(telegram_id):
        return False, "У вас уже есть активный запрос. Ожидайте ответа администратора."
    
    async with SessionLocal() as session:
        try:
            # Проверяем rate-limit: последний запрос должен быть старше 1 часа
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            
            result = await session.execute(
                select(AccessRequest).where(
                    and_(
                        AccessRequest.telegram_id == telegram_id,
                        AccessRequest.requested_at >= one_hour_ago
                    )
                ).order_by(AccessRequest.requested_at.desc())
            )
            recent_request = result.scalar_one_or_none()
            
            if recent_request:
                hours_passed = (datetime.utcnow() - recent_request.requested_at).total_seconds() / 3600
                hours_left = 1 - hours_passed
                minutes_left = int(hours_left * 60)
                if minutes_left > 0:
                    return False, f"Вы можете создать запрос только раз в 1 час. Попробуйте через {minutes_left} минут."
                else:
                    return False, f"Вы можете создать запрос только раз в 1 час. Попробуйте через несколько секунд."
            
            return True, None
            
        except Exception as e:
            logger.error(f"Ошибка при проверке rate-limit для пользователя {telegram_id}: {e}")
            return False, "Ошибка при проверке возможности создания запроса"


async def get_request_by_id(request_id: int) -> Optional[AccessRequest]:
    """
    Получает запрос по ID
    
    Args:
        request_id: ID запроса
        
    Returns:
        AccessRequest или None
    """
    if not SessionLocal:
        return None
    
    async with SessionLocal() as session:
        try:
            result = await session.execute(
                select(AccessRequest).where(AccessRequest.id == request_id)
            )
            return result.scalar_one_or_none()
            
        except Exception as e:
            logger.error(f"Ошибка при получении запроса {request_id}: {e}")
            return None


async def approve_request(request_id: int, approved_by: int) -> bool:
    """
    Одобряет запрос на доступ
    
    Args:
        request_id: ID запроса
        approved_by: Telegram ID администратора
        
    Returns:
        True если успешно, False иначе
    """
    if not SessionLocal:
        return False
    
    async with SessionLocal() as session:
        try:
            result = await session.execute(
                select(AccessRequest).where(AccessRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            
            if not request:
                logger.error(f"Запрос {request_id} не найден")
                return False
            
            if request.status != "pending":
                logger.warning(f"Запрос {request_id} уже обработан (статус: {request.status})")
                return False
            
            request.status = "approved"
            request.approved_at = datetime.utcnow()
            request.approved_by = approved_by
            
            await session.commit()
            await session.refresh(request)
            
            logger.info(f"Запрос {request_id} одобрен администратором {approved_by}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при одобрении запроса {request_id}: {e}")
            await session.rollback()
            return False


async def reject_request(request_id: int, approved_by: int) -> bool:
    """
    Отклоняет запрос на доступ
    
    Args:
        request_id: ID запроса
        approved_by: Telegram ID администратора
        
    Returns:
        True если успешно, False иначе
    """
    if not SessionLocal:
        return False
    
    async with SessionLocal() as session:
        try:
            result = await session.execute(
                select(AccessRequest).where(AccessRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            
            if not request:
                logger.error(f"Запрос {request_id} не найден")
                return False
            
            if request.status != "pending":
                logger.warning(f"Запрос {request_id} уже обработан (статус: {request.status})")
                return False
            
            request.status = "rejected"
            request.approved_at = datetime.utcnow()
            request.approved_by = approved_by
            
            await session.commit()
            await session.refresh(request)
            
            logger.info(f"Запрос {request_id} отклонен администратором {approved_by}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при отклонении запроса {request_id}: {e}")
            await session.rollback()
            return False


async def get_pending_requests(limit: int = 100) -> List[AccessRequest]:
    """
    Получает список всех pending-запросов
    
    Args:
        limit: Максимальное количество запросов
        
    Returns:
        Список AccessRequest
    """
    if not SessionLocal:
        return []
    
    async with SessionLocal() as session:
        try:
            result = await session.execute(
                select(AccessRequest)
                .where(AccessRequest.status == "pending")
                .order_by(AccessRequest.requested_at.asc())
                .limit(limit)
            )
            return list(result.scalars().all())
            
        except Exception as e:
            logger.error(f"Ошибка при получении pending-запросов: {e}")
            return []
