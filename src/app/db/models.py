from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import (
    String, BigInteger, DateTime, Boolean, Numeric, ForeignKey, func, Text, JSON, Integer,
    UniqueConstraint, Index,
)
from datetime import datetime
from typing import Optional, Dict, Any, List


class Base(DeclarativeBase):
    pass


class RemnaUser(Base):
    """Пользователи из Remna API"""
    __tablename__ = "remna_users"

    remna_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="ID пользователя из Remna API")
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    raw_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, comment="Полные данные из Remna API")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="Последняя синхронизация с Remna API")

    telegram_users: Mapped[list["TelegramUser"]] = relationship("TelegramUser", back_populates="remna_user", cascade="all, delete-orphan")
    subscriptions: Mapped[list["Subscription"]] = relationship("Subscription", back_populates="remna_user")


class TelegramUser(Base):
    """Пользователи Telegram"""
    __tablename__ = "telegram_users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="Telegram User ID")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    remna_user_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        ForeignKey("remna_users.remna_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Связь с пользователем Remna"
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=func.true(),
        nullable=False,
        index=True,
        comment="False если бот получил TelegramForbiddenError (юзер заблокировал бота)",
    )
    broadcast_opt_out: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=func.false(),
        nullable=False,
        index=True,
        comment="True если юзер отписался от рассылок (/stop или bc:unsub)",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="Последняя активность в боте")

    remna_user: Mapped[Optional["RemnaUser"]] = relationship("RemnaUser", back_populates="telegram_users")
    subscriptions: Mapped[list["Subscription"]] = relationship("Subscription", back_populates="telegram_user", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship("Payment", back_populates="telegram_user", cascade="all, delete-orphan")


class Subscription(Base):
    """Подписки на VPN"""
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="Код тарифа: basic, premium, pro")
    plan_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="Название тарифа")
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    is_lifetime: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="Подписка навсегда (admin grant forever)")
    last_expiry_notice_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="Время последнего уведомления об истечении (rate-limit 24h)"
    )
    config_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, comment="Данные конфигурации VPN")
    remna_subscription_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True, comment="ID подписки в Remna API")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )

    telegram_user: Mapped["TelegramUser"] = relationship("TelegramUser", back_populates="subscriptions")
    remna_user: Mapped[Optional["RemnaUser"]] = relationship("RemnaUser", back_populates="subscriptions")


class Payment(Base):
    """Платежи"""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="yookassa", index=True, comment="Провайдер платежей")
    external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True, comment="ID платежа во внешней системе")
    amount: Mapped[Numeric] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True, comment="pending, succeeded, canceled, failed")
    subscription_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payment_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, comment="Дополнительные данные платежа")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="Время успешной оплаты")

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
    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Telegram ID пользователя, запросившего доступ"
    )
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="Имя пользователя")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="Username пользователя")
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        nullable=False,
        index=True,
        comment="Статус: pending, approved, rejected"
    )
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
    approved_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
        comment="Telegram ID администратора, обработавшего запрос"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
        server_onupdate=func.now()
    )

    telegram_user: Mapped["TelegramUser"] = relationship("TelegramUser", foreign_keys=[telegram_id])


class Broadcast(Base):
    """Рассылки администратора по пользователям."""
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text_html: Mapped[str] = mapped_column(Text, nullable=False, comment="Текст сообщения в HTML")
    photo_file_id: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True, comment="Telegram file_id фото (опционально)"
    )
    buttons_json: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSON, nullable=True, comment="Массив {text, url | callback_data}"
    )
    segment: Mapped[str] = mapped_column(
        String(16), nullable=False, default="all",
        comment="Сегмент: all | active | expired | never",
    )
    disable_notification: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=func.false(), nullable=False,
    )
    created_by: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="Telegram ID админа-автора",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), server_default=func.now(), nullable=False, index=True,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    delivered: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    blocked: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    recipients: Mapped[list["BroadcastRecipient"]] = relationship(
        "BroadcastRecipient", back_populates="broadcast", cascade="all, delete-orphan",
    )


class BroadcastRecipient(Base):
    """Получатель конкретной рассылки."""
    __tablename__ = "broadcast_recipients"
    __table_args__ = (
        UniqueConstraint("broadcast_id", "user_telegram_id", name="uq_broadcast_recipient"),
        Index("ix_broadcast_recipients_bc_status", "broadcast_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broadcast_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("broadcasts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False,
        comment="pending | sent | failed | blocked",
    )
    error_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    broadcast: Mapped["Broadcast"] = relationship("Broadcast", back_populates="recipients")
