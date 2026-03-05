#!/bin/bash
# Скрипт для запуска всех операций одной командой
# Использование: ./run_all.sh [build|test-unit|test-integration|test-all|run|all]

set -e  # Остановка при ошибке

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функция для вывода сообщений
info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Проверка наличия Docker
if ! command -v docker &> /dev/null; then
    error "Docker не установлен. Установите Docker для продолжения."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    error "Docker Compose не установлен. Установите Docker Compose для продолжения."
    exit 1
fi

# Функция сборки
build() {
    info "Сборка Docker образов..."
    docker-compose build
    success "Сборка завершена"
}

# Функция запуска unit тестов
test_unit() {
    info "Запуск unit тестов..."
    
    # Запускаем Redis (unit тесты не требуют БД)
    docker-compose up -d redis
    sleep 5
    
    # Запускаем unit тесты в временном контейнере (без интеграционных)
    docker-compose run --rm bot sh -c "python3 -m pytest tests/ -v -m 'not integration'" || {
        error "Unit тесты провалились"
        return 1
    }
    
    success "Unit тесты пройдены"
}

# Функция запуска интеграционных тестов
test_integration() {
    info "Запуск интеграционных тестов..."
    
    # Запускаем тестовую БД и применяем миграции
    info "Запуск тестовой БД..."
    docker-compose -f docker-compose.test.yml up -d db_test redis_test
    
    # Ждем готовности БД
    info "Ожидание готовности тестовой БД..."
    timeout=30
    while [ $timeout -gt 0 ]; do
        if docker-compose -f docker-compose.test.yml exec -T db_test pg_isready -U crs_user_test -d crs_vpn_test &> /dev/null; then
            break
        fi
        sleep 1
        timeout=$((timeout - 1))
    done
    
    if [ $timeout -eq 0 ]; then
        error "Тестовая БД не готова"
        docker-compose -f docker-compose.test.yml down
        return 1
    fi
    
    success "Тестовая БД готова"
    
    # Применяем миграции к тестовой БД
    info "Применение миграций к тестовой БД..."
    docker-compose -f docker-compose.test.yml run --rm bot_test sh -c "
        echo 'Применение миграций к тестовой БД...' &&
        PYTHONPATH=/app/src python3 -m alembic upgrade head &&
        echo 'Миграции применены'
    " || {
        error "Ошибка применения миграций"
        docker-compose -f docker-compose.test.yml down
        return 1
    }
    
    # Устанавливаем переменную окружения для тестов
    # В контейнере используем db_test, на хосте localhost:5433
    export TEST_DATABASE_URL="postgresql+asyncpg://crs_user_test:crs_pass_test@db_test:5432/crs_vpn_test"
    
    # Запускаем интеграционные тесты в временном контейнере
    docker-compose -f docker-compose.test.yml run --rm bot_test sh -c "python3 -m pytest tests/integration/ -v -m 'integration'" || {
        error "Интеграционные тесты провалились"
        docker-compose -f docker-compose.test.yml down
        return 1
    }
    
    success "Интеграционные тесты пройдены"
    
    # Останавливаем тестовую БД
    info "Остановка тестовой БД..."
    docker-compose -f docker-compose.test.yml down
}

# Функция запуска всех тестов
test_all() {
    info "Запуск всех тестов (unit + integration)..."
    
    test_unit || return 1
    test_integration || return 1
    
    success "Все тесты пройдены"
}

# Функция запуска приложения
run() {
    info "Запуск приложения..."
    
    # Проверяем наличие .env файла
    if [ ! -f .env ]; then
        warning ".env файл не найден. Создаю из примера..."
        if [ -f config.example.env ]; then
            cp config.example.env .env
            warning "Пожалуйста, заполните .env файл перед запуском"
        else
            error "config.example.env не найден"
            return 1
        fi
    fi
    
    # BOT_MODE: legacy = db + migrate, no_db = только redis (env имеет приоритет над .env)
    BOT_MODE=${BOT_MODE:-$(grep -E '^BOT_MODE=' .env 2>/dev/null | cut -d= -f2)}
    BOT_MODE=${BOT_MODE:-legacy}
    export BOT_MODE  # Передать в docker-compose для подстановки в environment

    # Запускаем redis (всегда)
    docker-compose up -d redis
    info "Ожидание готовности redis..."
    sleep 5

    if [ "$BOT_MODE" = "legacy" ]; then
        info "Режим legacy: запуск db + миграции..."
        docker-compose --profile legacy up -d db
        info "Ожидание готовности db..."
        sleep 10
        info "Применение миграций..."
        docker-compose --profile legacy run --rm migrate || {
            error "Ошибка применения миграций"
            return 1
        }
    else
        info "Режим no_db: миграции не требуются"
    fi

    # Запускаем bot и webhook-api
    docker-compose up -d bot webhook-api

    info "Ожидание готовности сервисов..."
    sleep 5

    # Проверяем статус
    docker-compose ps
    
    success "Приложение запущено"
    info "Логи: docker-compose logs -f"
    info "Остановка: docker-compose down"
}

# Функция выполнения всех операций
all() {
    info "Выполнение всех операций: сборка -> тесты -> запуск"
    
    build || return 1
    test_all || return 1
    
    read -p "Запустить приложение? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        run
    else
        info "Приложение не запущено. Используйте './run_all.sh run' для запуска"
    fi
}

# Главная логика
case "${1:-all}" in
    build)
        build
        ;;
    test-unit)
        test_unit
        ;;
    test-integration)
        test_integration
        ;;
    test-all)
        test_all
        ;;
    run)
        run
        ;;
    all)
        all
        ;;
    *)
        echo "Использование: $0 [build|test-unit|test-integration|test-all|run|all]"
        echo ""
        echo "Команды:"
        echo "  build            - Сборка Docker образов"
        echo "  test-unit        - Запуск unit тестов"
        echo "  test-integration - Запуск интеграционных тестов"
        echo "  test-all         - Запуск всех тестов"
        echo "  run              - Запуск приложения"
        echo "  all              - Выполнить все: сборка -> тесты -> запуск (по умолчанию)"
        exit 1
        ;;
esac

