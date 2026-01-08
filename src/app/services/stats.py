"""Сервис для получения статистики"""
from datetime import datetime, timedelta
from typing import Dict, Any
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelegramUser, Subscription, Payment
from app.db.session import SessionLocal
from app.logger import logger


async def get_statistics() -> Dict[str, Any]:
    """Получает общую статистику бота"""
    if not SessionLocal:
        return {
            "total_users": 0,
            "active_subscriptions": 0,
            "total_payments": 0,
            "total_revenue": 0.0,
            "today_users": 0,
            "today_payments": 0,
            "today_revenue": 0.0
        }
    
    async with SessionLocal() as session:
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        total_users_result = await session.execute(
            select(func.count(TelegramUser.telegram_id))
        )
        total_users = total_users_result.scalar() or 0
        
        today_users_result = await session.execute(
            select(func.count(TelegramUser.telegram_id))
            .where(TelegramUser.created_at >= today_start)
        )
        today_users = today_users_result.scalar() or 0
        
        active_subs_result = await session.execute(
            select(func.count(Subscription.id))
            .where(
                Subscription.active == True,
                (Subscription.valid_until.is_(None)) | (Subscription.valid_until > now)
            )
        )
        active_subscriptions = active_subs_result.scalar() or 0
        
        total_payments_result = await session.execute(
            select(func.count(Payment.id))
        )
        total_payments = total_payments_result.scalar() or 0
        
        today_payments_result = await session.execute(
            select(func.count(Payment.id))
            .where(Payment.created_at >= today_start)
        )
        today_payments = today_payments_result.scalar() or 0
        
        total_revenue_result = await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "succeeded")
        )
        total_revenue = float(total_revenue_result.scalar() or 0)
        
        today_revenue_result = await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(
                Payment.status == "succeeded",
                Payment.created_at >= today_start
            )
        )
        today_revenue = float(today_revenue_result.scalar() or 0)
        
        return {
            "total_users": total_users,
            "active_subscriptions": active_subscriptions,
            "total_payments": total_payments,
            "total_revenue": total_revenue,
            "today_users": today_users,
            "today_payments": today_payments,
            "today_revenue": today_revenue
        }


async def get_users_list(page: int = 1, page_size: int = 10) -> Dict[str, Any]:
    """Получает список пользователей с пагинацией"""
    if not SessionLocal:
        return {
            "users": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 0
        }
    
    async with SessionLocal() as session:
        total_result = await session.execute(
            select(func.count(TelegramUser.telegram_id))
        )
        total = total_result.scalar() or 0
        
        offset = (page - 1) * page_size
        users_result = await session.execute(
            select(TelegramUser)
            .order_by(TelegramUser.created_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        users = users_result.scalars().all()
        
        users_data = []
        for user in users:
            now = datetime.utcnow()
            sub_result = await session.execute(
                select(Subscription)
                .where(
                    Subscription.telegram_user_id == user.telegram_id,
                    Subscription.active == True,
                    (Subscription.valid_until.is_(None)) | (Subscription.valid_until > now)
                )
                .order_by(Subscription.created_at.desc())
                .limit(1)
            )
            active_sub = sub_result.scalar_one_or_none()
            
            users_data.append({
                "telegram_id": user.telegram_id,
                "username": user.username or "без username",
                "first_name": user.first_name or "",
                "is_admin": user.is_admin,
                "has_active_subscription": active_sub is not None,
                "subscription_plan": active_sub.plan_code if active_sub else None,
                "created_at": user.created_at,
                "last_activity": user.last_activity_at
            })
        
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        
        return {
            "users": users_data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages
        }


async def get_payments_list(page: int = 1, page_size: int = 10, status: str = None) -> Dict[str, Any]:
    """Получает список платежей с пагинацией и фильтрацией"""
    if not SessionLocal:
        return {
            "payments": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 0
        }
    
    async with SessionLocal() as session:
        base_query = select(Payment)
        
        if status:
            base_query = base_query.where(Payment.status == status)
        
        count_query = select(func.count(Payment.id))
        if status:
            count_query = count_query.where(Payment.status == status)
        
        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0
        
        offset = (page - 1) * page_size
        payments_result = await session.execute(
            base_query
            .order_by(Payment.created_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        payments = payments_result.scalars().all()
        
        payments_data = []
        for payment in payments:
            user_result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == payment.telegram_user_id)
            )
            user = user_result.scalar_one_or_none()
            
            payments_data.append({
                "id": payment.id,
                "telegram_user_id": payment.telegram_user_id,
                "username": user.username if user else "неизвестно",
                "amount": float(payment.amount),
                "currency": payment.currency,
                "status": payment.status,
                "provider": payment.provider,
                "external_id": payment.external_id,
                "description": payment.description,
                "created_at": payment.created_at,
                "paid_at": payment.paid_at
            })
        
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        
        return {
            "payments": payments_data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status_filter": status
        }

