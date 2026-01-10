"""
Тесты для обработки ошибок RemnaClient
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import httpx
from app.remnawave.client import RemnaClient, RemnaEndpoints, _remna_circuit_breaker, CircuitState
from app.core.errors import InfraError


@pytest.fixture
async def remna_client():
    """Создает RemnaClient с мокнутым HTTP клиентом"""
    # Сбрасываем circuit breaker перед каждым тестом
    _remna_circuit_breaker.state = CircuitState.CLOSED
    _remna_circuit_breaker.failure_count = 0
    _remna_circuit_breaker.success_count = 0
    _remna_circuit_breaker.last_failure_time = None
    
    # Мокаем settings перед созданием клиента
    with patch('app.remnawave.client.settings') as mock_settings:
        mock_settings.remna_base_url = "https://api.test.com"
        mock_settings.remna_api_token = "test-token"
        mock_settings.REMNA_API_BASE = "https://api.test.com"
        mock_settings.REMNA_API_KEY = "test-token"
        
        client = RemnaClient(use_shared_client=False)
        # Устанавливаем base_url и api_key напрямую для тестов
        client.base_url = "https://api.test.com"
        client.api_key = "test-token"
        
        # Создаем мок для HTTP клиента
        mock_http_client = AsyncMock(spec=httpx.AsyncClient)
        client._own_client = mock_http_client
        yield client
        # Закрываем клиент после теста
        if client._own_client:
            try:
                await client.close()
            except:
                pass


@pytest.mark.asyncio
async def test_request_4xx_error_raises_infra_error(remna_client):
    """Тест: 4xx ошибки оборачиваются в InfraError"""
    # Мокаем HTTP ответ с 400 ошибкой
    error_response = MagicMock()
    error_response.status_code = 400
    error_response.text = "Bad Request: Invalid data"
    
    http_error = httpx.HTTPStatusError(
        "Bad Request",
        request=MagicMock(),
        response=error_response
    )
    
    remna_client._own_client.request = AsyncMock(side_effect=http_error)
    
    with pytest.raises(InfraError) as exc_info:
        await remna_client.request("GET", RemnaEndpoints.USERS)
    
    assert exc_info.value.service == "remna"
    error_msg = str(exc_info.value)
    assert "400" in error_msg or "Bad Request" in error_msg


@pytest.mark.asyncio
async def test_request_429_rate_limit_retries(remna_client):
    """Тест: 429 ошибка вызывает повторные попытки"""
    error_response = MagicMock()
    error_response.status_code = 429
    error_response.text = "Rate limit exceeded"
    
    http_error = httpx.HTTPStatusError(
        "Rate limit",
        request=MagicMock(),
        response=error_response
    )
    
    # Первая попытка - 429, вторая - успех
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json = MagicMock(return_value={"data": "success"})
    success_response.raise_for_status = MagicMock(return_value=None)
    
    remna_client._own_client.request = AsyncMock(side_effect=[
        http_error,
        success_response
    ])
    
    # Используем короткую задержку для теста
    remna_client.initial_delay = 0.01
    remna_client.max_retries = 2
    
    result = await remna_client.request("GET", RemnaEndpoints.USERS)
    
    assert result == {"data": "success"}
    assert remna_client._own_client.request.call_count == 2


@pytest.mark.asyncio
async def test_request_5xx_error_retries(remna_client):
    """Тест: 5xx ошибки вызывают повторные попытки"""
    error_response = MagicMock()
    error_response.status_code = 503
    error_response.text = "Service Unavailable"
    
    http_error = httpx.HTTPStatusError(
        "Service Unavailable",
        request=MagicMock(),
        response=error_response
    )
    
    # Все попытки возвращают 503
    remna_client._own_client.request = AsyncMock(side_effect=http_error)
    remna_client.max_retries = 2
    remna_client.initial_delay = 0.01
    
    with pytest.raises(InfraError) as exc_info:
        await remna_client.request("GET", RemnaEndpoints.USERS)
    
    assert exc_info.value.service == "remna"
    error_msg = str(exc_info.value)
    assert "503" in error_msg or "недоступен" in error_msg
    # Должно быть 3 попытки (max_retries + 1)
    assert remna_client._own_client.request.call_count == 3


@pytest.mark.asyncio
async def test_request_network_error_raises_infra_error(remna_client):
    """Тест: сетевые ошибки оборачиваются в InfraError"""
    network_error = httpx.RequestError(
        "Connection timeout",
        request=MagicMock()
    )
    
    remna_client._own_client.request = AsyncMock(side_effect=network_error)
    remna_client.max_retries = 1
    remna_client.initial_delay = 0.01
    
    with pytest.raises(InfraError) as exc_info:
        await remna_client.request("GET", RemnaEndpoints.USERS)
    
    assert exc_info.value.service == "remna"
    error_msg = str(exc_info.value).lower()
    assert "подключения" in error_msg or "connection" in error_msg


@pytest.mark.asyncio
async def test_request_timeout_raises_infra_error(remna_client):
    """Тест: таймауты оборачиваются в InfraError"""
    timeout_error = httpx.TimeoutException(
        "Request timeout",
        request=MagicMock()
    )
    
    remna_client._own_client.request = AsyncMock(side_effect=timeout_error)
    remna_client.max_retries = 1
    remna_client.initial_delay = 0.01
    
    with pytest.raises(InfraError) as exc_info:
        await remna_client.request("GET", RemnaEndpoints.USERS)
    
    assert exc_info.value.service == "remna"


@pytest.mark.asyncio
async def test_get_user_by_id_uses_endpoint_constant(remna_client):
    """Тест: методы используют константы endpoints"""
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json = MagicMock(return_value={"uuid": "test-uuid"})
    success_response.raise_for_status = MagicMock(return_value=None)
    
    remna_client._own_client.request = AsyncMock(return_value=success_response)
    
    await remna_client.get_user_by_id("test-uuid")
    
    # Проверяем, что был вызван request с правильным endpoint
    call_args = remna_client._own_client.request.call_args
    assert call_args is not None
    # call_args[0] - позиционные аргументы: (method, url, ...)
    # call_args[1] - именованные аргументы: {headers: ..., ...}
    url_arg = call_args[0][1] if len(call_args[0]) > 1 else str(call_args)
    assert "/api/users/test-uuid" in url_arg or RemnaEndpoints.USER_BY_ID.format(user_id="test-uuid") in url_arg


@pytest.mark.asyncio
async def test_create_user_uses_endpoint_constant(remna_client):
    """Тест: create_user использует константу endpoint"""
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json = MagicMock(return_value={"uuid": "new-uuid"})
    success_response.raise_for_status = MagicMock(return_value=None)
    
    remna_client._own_client.request = AsyncMock(return_value=success_response)
    
    await remna_client.create_user("test_user", "password123")
    
    # Проверяем, что был вызван request с правильным endpoint
    call_args = remna_client._own_client.request.call_args
    assert call_args is not None
    # Endpoint должен быть /api/users
    url_arg = call_args[0][1] if len(call_args[0]) > 1 else str(call_args)
    assert "/api/users" in url_arg or RemnaEndpoints.USERS in url_arg


@pytest.mark.asyncio
async def test_update_user_uses_endpoint_constant(remna_client):
    """Тест: update_user использует константу endpoint (не перебирает варианты)"""
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json = MagicMock(return_value={"uuid": "test-uuid"})
    success_response.raise_for_status = MagicMock(return_value=None)
    
    remna_client._own_client.request = AsyncMock(return_value=success_response)
    
    await remna_client.update_user("test-uuid", username="new_username")
    
    # Должен быть только один вызов (не перебор endpoint'ов)
    assert remna_client._own_client.request.call_count == 1
    
    # Endpoint должен быть правильным
    call_args = remna_client._own_client.request.call_args
    assert call_args is not None
    url_arg = call_args[0][1] if len(call_args[0]) > 1 else str(call_args)
    assert "/api/users/test-uuid" in url_arg or RemnaEndpoints.USER_BY_ID.format(user_id="test-uuid") in url_arg
