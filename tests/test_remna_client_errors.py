"""
Тесты для обработки ошибок RemnaClient
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from datetime import datetime, timezone
from app.remnawave.client import RemnaClient, LIFETIME_EXPIRE_AT, normalize_expire_at, build_user_payload_from_kwargs
from app.core.errors import InfraError


@pytest.fixture
async def remna_client():
    """Создает RemnaClient с мокнутым HTTP клиентом"""
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
async def test_request_4xx_error_raises_http_status_error(remna_client):
    """Тест: 4xx ошибки пробрасываются как HTTPStatusError"""
    error_response = MagicMock()
    error_response.status_code = 400
    error_response.text = "Bad Request: Invalid data"
    http_error = httpx.HTTPStatusError(
        "Bad Request",
        request=MagicMock(),
        response=error_response
    )
    remna_client._own_client.request = AsyncMock(side_effect=http_error)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await remna_client.request("GET", "/api/users")
    assert exc_info.value.response.status_code == 400


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
    
    result = await remna_client.request("GET", "/api/users")
    
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
    
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await remna_client.request("GET", "/api/users")
    assert exc_info.value.response.status_code == 503
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
        await remna_client.request("GET", "/api/users")
    
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
        await remna_client.request("GET", "/api/users")
    
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
    assert "/api/users/test-uuid" in url_arg or "/api/user/test-uuid" in url_arg


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
    assert "/api/users" in url_arg


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
    assert "/api/users/test-uuid" in url_arg or "/api/user/test-uuid" in url_arg


# --- Тесты expireAt / normalize_expire_at / build_user_payload ---

def test_normalize_expire_at_lifetime_datetime():
    """Навсегда (datetime 2099) -> LIFETIME_EXPIRE_AT"""
    dt = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert normalize_expire_at(dt) == LIFETIME_EXPIRE_AT


def test_normalize_expire_at_lifetime_str():
    """Строка с 2099 -> LIFETIME_EXPIRE_AT"""
    assert normalize_expire_at("2099-12-31T23:59:59+00:00") == LIFETIME_EXPIRE_AT
    assert normalize_expire_at("2099-12-31") == LIFETIME_EXPIRE_AT


def test_normalize_expire_at_none():
    """None -> None"""
    assert normalize_expire_at(None) is None


def test_normalize_expire_at_regular_date():
    """Обычная дата -> ISO8601 UTC с Z"""
    dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    result = normalize_expire_at(dt)
    assert result == "2025-06-15T12:00:00Z"


def test_normalize_expire_at_str_with_plus00_converts_to_z():
    """Строка с +00:00 -> канонический Z"""
    result = normalize_expire_at("2025-12-31T23:59:59+00:00")
    assert result == "2025-12-31T23:59:59Z"


def test_build_user_payload_expire_at_maps_to_expireAt():
    """expire_at в kwargs -> expireAt в payload"""
    payload = build_user_payload_from_kwargs({"expire_at": "2025-12-31T23:59:59Z"})
    assert "expireAt" in payload
    assert payload["expireAt"] == "2025-12-31T23:59:59Z"
    assert "expire_at" not in payload


def test_build_user_payload_unsupported_fields_ignored():
    """Неподдерживаемые поля не попадают в payload"""
    payload = build_user_payload_from_kwargs({
        "expire_at": "2025-01-01T00:00:00Z",
        "foo": "bar",
        "internal_stuff": 123,
    })
    assert "foo" not in payload
    assert "internal_stuff" not in payload
    assert "expireAt" in payload


def test_build_user_payload_expireAt_priority_over_expire_at():
    """Приоритет: expireAt > expire_at при одновременной передаче"""
    payload = build_user_payload_from_kwargs({
        "expire_at": "2025-01-01T00:00:00Z",
        "expireAt": "2026-06-15T12:00:00Z",
    })
    assert payload["expireAt"] == "2026-06-15T12:00:00Z"


@pytest.mark.asyncio
async def test_update_user_with_expire_at_sends_expireAt(remna_client):
    """update_user(expire_at=...) отправляет expireAt в json payload"""
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json = MagicMock(return_value={"uuid": "test-uuid"})
    success_response.raise_for_status = MagicMock(return_value=None)
    remna_client._own_client.request = AsyncMock(return_value=success_response)

    await remna_client.update_user("test-uuid", expire_at=datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc))

    call_args = remna_client._own_client.request.call_args
    assert call_args is not None
    json_payload = call_args[1].get("json", {})
    assert "expireAt" in json_payload
    assert json_payload["expireAt"] == "2025-12-31T23:59:59Z"
    assert "expire_at" not in json_payload


@pytest.mark.asyncio
async def test_create_user_lifetime_sends_2099(remna_client):
    """create_user с датой 2099 отправляет LIFETIME_EXPIRE_AT"""
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json = MagicMock(return_value={"uuid": "new-uuid"})
    success_response.raise_for_status = MagicMock(return_value=None)
    remna_client._own_client.request = AsyncMock(return_value=success_response)

    await remna_client.create_user(
        "test_user", "password123",
        expire_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    )

    call_args = remna_client._own_client.request.call_args
    json_payload = call_args[1].get("json", {})
    assert json_payload["expireAt"] == LIFETIME_EXPIRE_AT
