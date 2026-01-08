#!/bin/bash
# Скрипт для запуска тестов в Docker
# Использование: ./run_tests_docker.sh [unit|integration|all]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Проверка Docker
if ! command -v docker &> /dev/null; then
    echo "Ошибка: Docker не найден. Установите Docker для запуска тестов."
    exit 1
fi

if ! docker compose version &> /dev/null && ! docker-compose version &> /dev/null; then
    echo "Ошибка: Docker Compose не найден."
    exit 1
fi

case "${1:-all}" in
    unit)
        echo "Запуск unit тестов в Docker..."
        docker compose up -d db redis
        sleep 3
        docker compose exec bot python3 -m pytest tests/ -v -m "not integration"
        ;;
    integration)
        echo "Запуск интеграционных тестов в Docker..."
        docker compose -f docker-compose.test.yml up -d
        sleep 5
        docker compose -f docker-compose.test.yml exec bot_test python3 -m pytest tests/integration/ -v -m "integration"
        docker compose -f docker-compose.test.yml down
        ;;
    all)
        echo "Запуск всех тестов в Docker..."
        docker compose up -d db redis
        sleep 3
        echo "Unit тесты:"
        docker compose exec bot python3 -m pytest tests/ -v -m "not integration"
        echo ""
        echo "Интеграционные тесты:"
        docker compose -f docker-compose.test.yml up -d
        sleep 5
        docker compose -f docker-compose.test.yml exec bot_test python3 -m pytest tests/integration/ -v -m "integration"
        docker compose -f docker-compose.test.yml down
        ;;
    *)
        echo "Использование: $0 [unit|integration|all]"
        exit 1
        ;;
esac

