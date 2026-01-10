"""
Тесты для поиска пользователей в Remna API
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.remnawave.client import RemnaClient, RemnaUser


@pytest.fixture
def mock_remna_client():
    """Создает мок RemnaClient"""
    client = RemnaClient()
    client.request = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_found_on_first_page(mock_remna_client):
    """Тест: пользователь найден на первой странице"""
    target_telegram_id = 5628460233
    
    # Мокаем ответ API - пользователь на первой странице
    mock_response = {
        'response': {
            'users': [
                {
                    'uuid': 'user-123',
                    'telegramId': 520258426,
                    'username': 'user1',
                    'name': 'User One'
                },
                {
                    'uuid': 'user-456',
                    'telegramId': target_telegram_id,  # Искомый пользователь
                    'username': 'dukrmv638',
                    'name': 'HDT6N93B'
                },
                {
                    'uuid': 'user-789',
                    'telegramId': 833870963,
                    'username': 'user3',
                    'name': 'User Three'
                }
            ]
        }
    }
    
    mock_remna_client.request.return_value = mock_response
    
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    
    assert result is not None
    assert isinstance(result, RemnaUser)
    assert result.uuid == 'user-456'
    assert result.telegram_id == target_telegram_id
    assert result.username == 'dukrmv638'
    assert mock_remna_client.request.call_count == 1


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_not_found(mock_remna_client):
    """Тест: пользователь не найден"""
    target_telegram_id = 999999999
    
    # Мокаем ответ API - пользователя нет
    mock_response = {
        'response': {
            'users': [
                {
                    'uuid': 'user-123',
                    'telegramId': 520258426,
                    'username': 'user1'
                },
                {
                    'uuid': 'user-456',
                    'telegramId': 5628460233,
                    'username': 'user2'
                }
            ]
        }
    }
    
    mock_remna_client.request.return_value = mock_response
    
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    
    assert result is None


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_found_on_second_page(mock_remna_client):
    """Тест: пользователь найден на второй странице"""
    target_telegram_id = 5628460233
    
    # Первая страница - пользователя нет
    first_page_response = {
        'response': {
            'users': [
                {
                    'uuid': f'user-{i}',
                    'telegramId': 1000000000 + i,
                    'username': f'user{i}'
                }
                for i in range(100)  # Полная страница
            ]
        }
    }
    
    # Вторая страница - пользователь есть
    second_page_response = {
        'response': {
            'users': [
                {
                    'uuid': 'user-found',
                    'telegramId': target_telegram_id,
                    'username': 'dukrmv638',
                    'name': 'HDT6N93B'
                }
            ]
        }
    }
    
    # Третья страница - пустая
    third_page_response = {
        'response': {
            'users': []
        }
    }
    
    mock_remna_client.request.side_effect = [
        first_page_response,
        second_page_response,
        third_page_response
    ]
    
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    
    assert result is not None
    assert result.uuid == 'user-found'
    assert result.telegram_id == target_telegram_id
    assert mock_remna_client.request.call_count == 2  # Проверены 2 страницы


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_with_missing_telegram_id(mock_remna_client):
    """Тест: пользователи без telegramId пропускаются"""
    target_telegram_id = 5628460233
    
    mock_response = {
        'response': {
            'users': [
                {
                    'uuid': 'user-1',
                    'username': 'user1',
                    # Нет telegramId
                },
                {
                    'uuid': 'user-2',
                    'telegramId': target_telegram_id,
                    'username': 'user2'
                },
                {
                    'uuid': 'user-3',
                    'username': 'user3',
                    # Нет telegramId
                }
            ]
        }
    }
    
    mock_remna_client.request.return_value = mock_response
    
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    
    assert result is not None
    assert result.uuid == 'user-2'
    assert result.telegram_id == target_telegram_id


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_string_telegram_id(mock_remna_client):
    """Тест: telegramId может быть строкой"""
    target_telegram_id = 5628460233
    
    mock_response = {
        'response': {
            'users': [
                {
                    'uuid': 'user-1',
                    'telegramId': str(target_telegram_id),  # Строка!
                    'username': 'user1'
                }
            ]
        }
    }
    
    mock_remna_client.request.return_value = mock_response
    
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    
    assert result is not None
    assert result.telegram_id == target_telegram_id


@pytest.mark.asyncio
async def test_get_user_by_telegram_id_different_response_formats(mock_remna_client):
    """Тест: обработка разных форматов ответа API"""
    target_telegram_id = 5628460233
    
    # Тест 1: формат с 'response' -> 'users'
    response1 = {
        'response': {
            'users': [
                {
                    'uuid': 'user-1',
                    'telegramId': target_telegram_id,
                    'username': 'user1'
                }
            ]
        }
    }
    
    mock_remna_client.request.return_value = response1
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    assert result is not None
    
    # Тест 2: формат с прямым списком
    response2 = [
        {
            'uuid': 'user-2',
            'telegramId': target_telegram_id,
            'username': 'user2'
        }
    ]
    
    mock_remna_client.request.return_value = response2
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    assert result is not None
    
    # Тест 3: формат с 'items'
    response3 = {
        'response': {
            'items': [
                {
                    'uuid': 'user-3',
                    'telegramId': target_telegram_id,
                    'username': 'user3'
                }
            ]
        }
    }
    
    mock_remna_client.request.return_value = response3
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    assert result is not None
