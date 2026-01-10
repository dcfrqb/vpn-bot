#!/bin/bash
# Комплексное тестирование систем на продакшене

set -e

echo "=========================================="
echo "КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ СИСТЕМ"
echo "=========================================="
echo ""

# Цвета для вывода
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Функция для проверки статуса
check_status() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $1"
        return 0
    else
        echo -e "${RED}✗${NC} $1"
        return 1
    fi
}

# 1. Проверка доступности контейнеров
echo "1. ПРОВЕРКА КОНТЕЙНЕРОВ"
echo "----------------------"
docker ps --filter "name=crs_vpn" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""

# 2. Проверка Webhook API
echo "2. ТЕСТИРОВАНИЕ WEBHOOK API"
echo "---------------------------"
echo -n "Проверка корневого эндпоинта: "
curl -s -f http://localhost:8001/ > /dev/null
check_status "Webhook API доступен"

echo -n "Проверка health endpoint: "
HEALTH=$(curl -s http://localhost:8001/health)
if echo "$HEALTH" | grep -q "healthy"; then
    echo -e "${GREEN}✓${NC} Health check пройден"
    echo "   Ответ: $HEALTH"
else
    echo -e "${RED}✗${NC} Health check не пройден"
    echo "   Ответ: $HEALTH"
fi
echo ""

# 3. Тестирование YouKassa webhook endpoint
echo "3. ТЕСТИРОВАНИЕ YOUKASSA WEBHOOK"
echo "---------------------------------"
echo -n "Тест webhook endpoint (пустой запрос): "
WEBHOOK_RESPONSE=$(curl -s -X POST http://localhost:8001/webhook/yookassa \
    -H "Content-Type: application/json" \
    -d '{}' 2>&1)
if echo "$WEBHOOK_RESPONSE" | grep -q "Empty request body\|Invalid JSON\|error"; then
    echo -e "${GREEN}✓${NC} Endpoint отвечает (ожидаемая ошибка для пустого запроса)"
else
    echo -e "${YELLOW}⚠${NC} Неожиданный ответ: $WEBHOOK_RESPONSE"
fi

echo -n "Тест webhook endpoint (валидный формат): "
TEST_WEBHOOK='{
    "type": "notification",
    "event": "payment.succeeded",
    "object": {
        "id": "test_payment_123",
        "status": "succeeded",
        "amount": {
            "value": "99.00",
            "currency": "RUB"
        },
        "description": "Test payment",
        "metadata": {
            "tg_user_id": "123456789"
        }
    }
}'
WEBHOOK_RESPONSE=$(curl -s -X POST http://localhost:8001/webhook/yookassa \
    -H "Content-Type: application/json" \
    -d "$TEST_WEBHOOK" 2>&1)
if echo "$WEBHOOK_RESPONSE" | grep -q "ok\|error"; then
    echo -e "${GREEN}✓${NC} Endpoint обрабатывает запросы"
    echo "   Ответ: $WEBHOOK_RESPONSE"
else
    echo -e "${YELLOW}⚠${NC} Неожиданный ответ: $WEBHOOK_RESPONSE"
fi
echo ""

# 4. Проверка логов на ошибки
echo "4. ПРОВЕРКА ЛОГОВ"
echo "-----------------"
echo "Последние 20 строк логов webhook API:"
docker logs crs_vpn_webhook_api --tail 20 2>&1 | tail -20
echo ""
echo "Последние 20 строк логов бота:"
docker logs crs_vpn_bot --tail 20 2>&1 | tail -20
echo ""

# 5. Тестирование Remnawave интеграции
echo "5. ТЕСТИРОВАНИЕ REMNAWAVE"
echo "-------------------------"
echo "Запуск тестов Remnawave клиента..."
docker compose exec -T bot python3 -m pytest tests/test_remna_client_errors.py -v --tb=short 2>&1 | head -50
echo ""

# 6. Тестирование YouKassa интеграции
echo "6. ТЕСТИРОВАНИЕ YOUKASSA"
echo "------------------------"
echo "Запуск тестов YouKassa..."
docker compose exec -T bot python3 -m pytest tests/test_payments.py -v --tb=short 2>&1 | head -100
echo ""

# 7. Проверка подключения к БД
echo "7. ПРОВЕРКА БАЗЫ ДАННЫХ"
echo "-----------------------"
echo -n "Проверка подключения к PostgreSQL: "
docker compose exec -T db pg_isready -U crs_user > /dev/null 2>&1
check_status "PostgreSQL доступен"

echo -n "Проверка подключения к Redis: "
docker compose exec -T redis redis-cli ping > /dev/null 2>&1
check_status "Redis доступен"
echo ""

# 8. Проверка синхронизации
echo "8. ТЕСТИРОВАНИЕ СИНХРОНИЗАЦИИ"
echo "-----------------------------"
echo "Запуск тестов синхронизации..."
docker compose exec -T bot python3 -m pytest tests/test_sync_service.py -v --tb=short 2>&1 | head -50
echo ""

echo "=========================================="
echo "ТЕСТИРОВАНИЕ ЗАВЕРШЕНО"
echo "=========================================="
