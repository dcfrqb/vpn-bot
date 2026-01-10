from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, BigInteger, DateTime, Boolean, Numeric, ForeignKey, func, Text, JSON, Integer
from datetime import datetime
from typing import Optional, Dict, Any


class Base(DeclarativeBase):
    pass


class RemnaUser(Base):
    """Пользователи из Remna API"""
    __tablename__ = "remna_users"
    
    # ID из Remna API (может быть строкой)
    remna_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="ID пользователя из Remna API")
    
    # Основные поля
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    
    # Дополнительные данные из API (храним как JSON для гибкости)
    raw_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, comment="Полные данные из Remna API")
    
    # Метаданные
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=func.now(), 
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="Последняя синхронизация с Remna API")
    
    # Связи
    telegram_users: Mapped[list["TelegramUser"]] = relationship("TelegramUser", back_populates="remna_user", cascade="all, delete-orphan")
    subscriptions: Mapped[list["Subscription"]] = relationship("Subscription", back_populates="remna_user")


class TelegramUser(Base):
    """Пользователи Telegram"""
    __tablename__ = "telegram_users"
    
    # Telegram ID (primary key)
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="Telegram User ID")
    
    # Основные поля Telegram
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    
    # Связь с Remna пользователем
    remna_user_id: Mapped[Optional[str]] = mapped_column(
        String(64), 
        ForeignKey("remna_users.remna_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Связь с пользователем Remna"
    )
    
    # Права доступа
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    
    # Метаданные
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="Последняя активность в боте")
    
    # Связи
    remna_user: Mapped[Optional["RemnaUser"]] = relationship("RemnaUser", back_populates="telegram_users")
    subscriptions: Mapped[list["Subscription"]] = relationship("Subscription", back_populates="telegram_user", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship("Payment", back_populates="telegram_user", cascade="all, delete-orphan")


class Subscription(Base):
    """Подписки на VPN"""
    __tablename__ = "subscriptions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Связи с пользователями
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    remna_user_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("remna_users.remna_id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # Данные подписки
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="Код тарифа: basic, premium, pro")
    plan_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="Название тарифа")
    
    # Статус
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    
    # Дополнительные данные
    config_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, comment="Данные конфигурации VPN")
    remna_subscription_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True, comment="ID подписки в Remna API")
    
    # Метаданные
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    
    # Связи
    telegram_user: Mapped["TelegramUser"] = relationship("TelegramUser", back_populates="subscriptions")
    remna_user: Mapped[Optional["RemnaUser"]] = relationship("RemnaUser", back_populates="subscriptions")


class Payment(Base):
    """Платежи"""
    __tablename__ = "payments"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Связь с пользователем
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Данные платежа
    provider: Mapped[str] = mapped_column(String(32), default="yookassa", index=True, comment="Провайдер платежей")
    external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True, comment="ID платежа во внешней системе")
    
    # Сумма
    amount: Mapped[Numeric] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    
    # Статус
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True, comment="pending, succeeded, canceled, failed")
    
    # Связь с подпиской (если платеж создал подписку)
    subscription_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # Дополнительные данные
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payment_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, comment="Дополнительные данные платежа")
    
    # Метаданные
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="Время успешной оплаты")
    
    # Связи
    telegram_user: Mapped["TelegramUser"] = relationship("TelegramUser", back_populates="payments")
    subscription: Mapped[Optional["Subscription"]] = relationship("Subscription", foreign_keys=[subscription_id])


class Squad(Base):
    """Сквады из Remna API (если нужны)"""
    __tablename__ = "squads"
    
    remna_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="ID сквада из Remna API")
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    raw_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Node(Base):
    """Ноды из Remna API (если нужны)"""
    __tablename__ = "nodes"
    
    remna_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="ID ноды из Remna API")
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    raw_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AccessRequest(Base):
    """Запросы на выдачу VPN-доступа администратором"""
    __tablename__ = "access_requests"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Связь с пользователем
    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Telegram ID пользователя, запросившего доступ"
    )
    
    # Данные пользователя на момент запроса
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="Имя пользователя")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="Username пользователя")
    
    # Статус запроса
    status: Mapped[str] = mapped_column(
        String(16), 
        default="pending", 
        nullable=False, 
        index=True,
        comment="Статус: pending, approved, rejected"
    )
    
    # Временные метки
    requested_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=func.now(), 
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="Время создания запроса"
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, 
        nullable=True,
        comment="Время одобрения/отклонения запроса"
    )
    
    # Администратор, обработавший запрос
    approved_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="Telegram ID администратора, обработавшего запрос"
    )
    
    # Метаданные
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    
    # Связи
    telegram_user: Mapped["TelegramUser"] = relationship("TelegramUser", foreign_keys=[telegram_id])
