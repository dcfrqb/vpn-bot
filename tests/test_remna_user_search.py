"""
Тесты для поиска пользователей в Remna API
Обновлено для API 2.6.x с прямыми эндпоинтами: GET /api/users/by-telegram-id/{telegramId}
"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from app.remnawave.client import RemnaClient, RemnaUser


@pytest.fixture
def mock_remna_client():
    """Создает мок RemnaClient"""
    client = RemnaClient()
    client.request = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_found(mock_remna_client):
    """Тест: пользователь найден через прямой эндпоинт"""
    target_telegram_id = 5628460233

    # Мокаем ответ API - прямой эндпоинт возвращает одного пользователя
    mock_response = {
        'response': {
            'uuid': 'user-456',
            'telegramId': target_telegram_id,
            'username': 'dukrmv638',
            'name': 'HDT6N93B'
        }
    }

    mock_remna_client.request.return_value = mock_response

    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)

    assert result is not None
    assert isinstance(result, RemnaUser)
    assert result.uuid == 'user-456'
    assert result.telegram_id == target_telegram_id
    assert result.username == 'dukrmv638'
    # Проверяем, что вызван правильный эндпоинт
    mock_remna_client.request.assert_called_once_with("GET", f"/api/users/by-telegram-id/{target_telegram_id}")


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_not_found(mock_remna_client):
    """Тест: пользователь не найден (404)"""
    target_telegram_id = 999999999

    # Мокаем 404 ответ
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "User not found"

    mock_remna_client.request.side_effect = httpx.HTTPStatusError(
        "404 Not Found",
        request=MagicMock(),
        response=mock_response
    )

    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)

    assert result is None


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_empty_response(mock_remna_client):
    """Тест: пустой ответ от API"""
    target_telegram_id = 5628460233

    mock_remna_client.request.return_value = {}

    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)

    assert result is None


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_list_response(mock_remna_client):
    """Тест: API возвращает список (берем первого)"""
    target_telegram_id = 5628460233

    # Некоторые API могут возвращать список даже для одного пользователя
    mock_response = {
        'response': [
            {
                'uuid': 'user-456',
                'telegramId': target_telegram_id,
                'username': 'dukrmv638',
                'name': 'HDT6N93B'
            }
        ]
    }

    mock_remna_client.request.return_value = mock_response

    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)

    assert result is not None
    assert result.uuid == 'user-456'
    assert result.telegram_id == target_telegram_id


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_without_uuid(mock_remna_client):
    """Тест: пользователь найден, но без uuid"""
    target_telegram_id = 5628460233

    mock_response = {
        'response': {
            'telegramId': target_telegram_id,
            'username': 'user1',
            # Нет uuid!
        }
    }

    mock_remna_client.request.return_value = mock_response

    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)

    assert result is None  # Без uuid не можем вернуть пользователя


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_direct_response(mock_remna_client):
    """Тест: API возвращает объект напрямую без 'response' обертки"""
    target_telegram_id = 5628460233

    mock_response = {
        'uuid': 'user-456',
        'telegramId': target_telegram_id,
        'username': 'dukrmv638',
        'name': 'HDT6N93B'
    }

    mock_remna_client.request.return_value = mock_response

    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)

    assert result is not None
    assert result.uuid == 'user-456'
    assert result.telegram_id == target_telegram_id
