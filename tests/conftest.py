"""Конфигурация pytest для тестов. БД удалена."""
import pytest
import asyncio
import os


@pytest.fixture(scope="function")
def mock_bot():
    """Мок бота для тестов"""
    from unittest.mock import AsyncMock

    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.answer_callback_query = AsyncMock()
    return bot


@pytest.fixture(scope="function")
def mock_remna_client():
    """Мок клиента Remna API"""
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.get_users = AsyncMock(return_value={"response": {"users": []}})
    client.get_user_subscription_url = AsyncMock(return_value="https://remna.example.com/subscription/123")
    client.create_user = AsyncMock(return_value={"uuid": "test-uuid-123"})
    client.close = AsyncMock()
    return client
