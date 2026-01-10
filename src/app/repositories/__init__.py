"""Репозитории для работы с БД"""
from .user_repo import UserRepo
from .subscription_repo import SubscriptionRepo

__all__ = ["UserRepo", "SubscriptionRepo"]
