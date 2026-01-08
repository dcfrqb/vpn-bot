#!/bin/bash
#
# Скрипт для переноса базы данных PostgreSQL с локального компьютера на сервер
# 
# Использование:
#   Локально (экспорт): ./migrate_database.sh export
#   На сервере (импорт): ./migrate_database.sh import
#

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Файл для дампа
DUMP_FILE="crs_vpn_dump_$(date +%Y%m%d_%H%M%S).sql"
COMPRESSED_DUMP="${DUMP_FILE}.gz"

# Параметры локальной БД (измените при необходимости)
LOCAL_DB_HOST="${LOCAL_DB_HOST:-localhost}"
LOCAL_DB_PORT="${LOCAL_DB_PORT:-5432}"
LOCAL_DB_NAME="${LOCAL_DB_NAME:-crs_vpn_bot}"
LOCAL_DB_USER="${LOCAL_DB_USER:-will}"
LOCAL_DB_PASSWORD="${LOCAL_DB_PASSWORD:-}"

# Параметры серверной БД (измените при необходимости)
SERVER_DB_HOST="${SERVER_DB_HOST:-localhost}"
SERVER_DB_PORT="${SERVER_DB_PORT:-5432}"
SERVER_DB_NAME="${SERVER_DB_NAME:-crs_vpn}"
SERVER_DB_USER="${SERVER_DB_USER:-crs_user}"
SERVER_DB_PASSWORD="${SERVER_DB_PASSWORD:-crs_pass}"

print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

check_pg_dump() {
    if ! command -v pg_dump &> /dev/null; then
        print_error "pg_dump не найден. Установите PostgreSQL client tools."
        exit 1
    fi
}

check_pg_restore() {
    if ! command -v psql &> /dev/null; then
        print_error "psql не найден. Установите PostgreSQL client tools."
        exit 1
    fi
}

export_database() {
    print_header "Экспорт базы данных с локального компьютера"
    
    check_pg_dump
    
    print_info "Параметры подключения:"
    echo "  Host: $LOCAL_DB_HOST"
    echo "  Port: $LOCAL_DB_PORT"
    echo "  Database: $LOCAL_DB_NAME"
    echo "  User: $LOCAL_DB_USER"
    echo ""
    
    # Проверка подключения
    print_info "Проверка подключения к локальной БД..."
    if [ -n "$LOCAL_DB_PASSWORD" ]; then
        export PGPASSWORD="$LOCAL_DB_PASSWORD"
    else
        unset PGPASSWORD
    fi
    if ! psql -h "$LOCAL_DB_HOST" -p "$LOCAL_DB_PORT" -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" -c "SELECT 1;" > /dev/null 2>&1; then
        print_error "Не удалось подключиться к локальной БД!"
        print_info "Проверьте параметры подключения или установите переменные окружения:"
        echo "  export LOCAL_DB_HOST=localhost"
        echo "  export LOCAL_DB_PORT=5432"
        echo "  export LOCAL_DB_NAME=crs_vpn_bot"
        echo "  export LOCAL_DB_USER=will"
        echo "  export LOCAL_DB_PASSWORD=your_password  # или оставьте пустым для peer auth"
        exit 1
    fi
    print_success "Подключение к локальной БД успешно"
    
    # Создание дампа
    print_info "Создание дампа базы данных..."
    if [ -n "$LOCAL_DB_PASSWORD" ]; then
        export PGPASSWORD="$LOCAL_DB_PASSWORD"
    else
        unset PGPASSWORD
    fi
    pg_dump -h "$LOCAL_DB_HOST" -p "$LOCAL_DB_PORT" -U "$LOCAL_DB_USER" -d "$LOCAL_DB_NAME" \
        --clean --if-exists --create --format=plain \
        --no-owner --no-privileges \
        -f "$DUMP_FILE" 2>&1 | grep -v "WARNING" || true
    
    if [ ! -f "$DUMP_FILE" ]; then
        print_error "Не удалось создать дамп!"
        exit 1
    fi
    
    # Сжатие
    print_info "Сжатие дампа..."
    gzip -f "$DUMP_FILE"
    
    if [ ! -f "$COMPRESSED_DUMP" ]; then
        print_error "Не удалось сжать дамп!"
        exit 1
    fi
    
    DUMP_SIZE=$(du -h "$COMPRESSED_DUMP" | cut -f1)
    print_success "Дамп создан: $COMPRESSED_DUMP (размер: $DUMP_SIZE)"
    
    print_info "Следующие шаги:"
    echo "  1. Скопируйте файл $COMPRESSED_DUMP на сервер"
    echo "  2. На сервере выполните: ./migrate_database.sh import"
    echo "  3. Или используйте: scp $COMPRESSED_DUMP user@server:/path/to/TGBot/"
}

import_database() {
    print_header "Импорт базы данных на сервер"
    
    check_pg_restore
    
    # Поиск файла дампа
    if [ -n "$1" ]; then
        DUMP_FILE="$1"
    else
        # Ищем последний дамп
        DUMP_FILE=$(ls -t crs_vpn_dump_*.sql.gz 2>/dev/null | head -n 1)
    fi
    
    if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
        print_error "Файл дампа не найден!"
        print_info "Укажите путь к файлу дампа:"
        echo "  ./migrate_database.sh import /path/to/dump.sql.gz"
        exit 1
    fi
    
    print_info "Используется файл: $DUMP_FILE"
    
    # Распаковка
    if [[ "$DUMP_FILE" == *.gz ]]; then
        print_info "Распаковка дампа..."
        gunzip -c "$DUMP_FILE" > "${DUMP_FILE%.gz}"
        SQL_FILE="${DUMP_FILE%.gz}"
    else
        SQL_FILE="$DUMP_FILE"
    fi
    
    print_info "Параметры подключения к серверной БД:"
    echo "  Host: $SERVER_DB_HOST"
    echo "  Port: $SERVER_DB_PORT"
    echo "  Database: $SERVER_DB_NAME"
    echo "  User: $SERVER_DB_USER"
    echo ""
    
    # Проверка подключения
    print_info "Проверка подключения к серверной БД..."
    if [ -n "$SERVER_DB_PASSWORD" ]; then
        export PGPASSWORD="$SERVER_DB_PASSWORD"
    else
        unset PGPASSWORD
    fi
    if ! psql -h "$SERVER_DB_HOST" -p "$SERVER_DB_PORT" -U "$SERVER_DB_USER" -d postgres -c "SELECT 1;" > /dev/null 2>&1; then
        print_error "Не удалось подключиться к серверной БД!"
        print_info "Если БД в Docker, используйте:"
        echo "  export SERVER_DB_HOST=localhost"
        echo "  export SERVER_DB_PORT=5432"
        echo "  export SERVER_DB_NAME=crs_vpn"
        echo "  export SERVER_DB_USER=crs_user"
        echo "  export SERVER_DB_PASSWORD=crs_pass"
        print_info "Или подключитесь через Docker:"
        echo "  docker exec -i crs-vpn-bot-db-1 psql -U crs_user -d crs_vpn < $SQL_FILE"
        exit 1
    fi
    print_success "Подключение к серверной БД успешно"
    
    # Восстановление
    print_warning "ВНИМАНИЕ: Это удалит все существующие данные в БД $SERVER_DB_NAME!"
    read -p "Продолжить? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        print_info "Операция отменена"
        exit 0
    fi
    
    print_info "Восстановление базы данных..."
    if [ -n "$SERVER_DB_PASSWORD" ]; then
        export PGPASSWORD="$SERVER_DB_PASSWORD"
    else
        unset PGPASSWORD
    fi
    
    # Удаляем старую БД и создаем новую
    psql -h "$SERVER_DB_HOST" -p "$SERVER_DB_PORT" -U "$SERVER_DB_USER" -d postgres \
        -c "DROP DATABASE IF EXISTS $SERVER_DB_NAME;" 2>&1 | grep -v "does not exist" || true
    
    # Импортируем дамп
    psql -h "$SERVER_DB_HOST" -p "$SERVER_DB_PORT" -U "$SERVER_DB_USER" -d postgres \
        -f "$SQL_FILE" 2>&1 | grep -v "WARNING" || true
    
    # Очистка временного файла
    if [[ "$DUMP_FILE" == *.gz ]]; then
        rm -f "$SQL_FILE"
    fi
    
    print_success "База данных успешно импортирована!"
    
    # Проверка
    print_info "Проверка импортированных данных..."
    if [ -n "$SERVER_DB_PASSWORD" ]; then
        export PGPASSWORD="$SERVER_DB_PASSWORD"
    else
        unset PGPASSWORD
    fi
    TABLE_COUNT=$(psql -h "$SERVER_DB_HOST" -p "$SERVER_DB_PORT" -U "$SERVER_DB_USER" -d "$SERVER_DB_NAME" \
        -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null | tr -d ' ')
    
    if [ -n "$TABLE_COUNT" ] && [ "$TABLE_COUNT" -gt 0 ]; then
        print_success "Найдено таблиц: $TABLE_COUNT"
    else
        print_warning "Не удалось проверить количество таблиц"
    fi
    
    print_info "Следующие шаги:"
    echo "  1. Проверьте подключение: docker-compose exec bot python3 -m app.scripts.check_connections"
    echo "  2. Примените миграции (если нужно): docker-compose exec bot alembic upgrade head"
    echo "  3. Перезапустите контейнеры: docker-compose restart bot webhook-api"
}

import_database_docker() {
    print_header "Импорт базы данных через Docker"
    
    # Поиск файла дампа
    if [ -n "$1" ]; then
        DUMP_FILE="$1"
    else
        DUMP_FILE=$(ls -t crs_vpn_dump_*.sql.gz 2>/dev/null | head -n 1)
    fi
    
    if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
        print_error "Файл дампа не найден!"
        exit 1
    fi
    
    print_info "Используется файл: $DUMP_FILE"
    
    # Определяем команду docker compose
    if command -v docker > /dev/null && docker compose version > /dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="docker compose"
    else
        DOCKER_COMPOSE_CMD="docker-compose"
    fi
    
    # Проверка контейнера
    if ! docker ps | grep -q "crs-vpn-bot-db-1\|crs-vpn-bot_db_1"; then
        print_error "Контейнер базы данных не запущен!"
        print_info "Запустите: $DOCKER_COMPOSE_CMD up -d db"
        exit 1
    fi
    
    DB_CONTAINER=$(docker ps | grep "crs-vpn-bot-db-1\|crs-vpn-bot_db_1" | awk '{print $1}')
    if [ -z "$DB_CONTAINER" ]; then
        print_error "Не удалось найти контейнер базы данных!"
        exit 1
    fi
    print_info "Найден контейнер БД: $DB_CONTAINER"
    
    print_warning "ВНИМАНИЕ: Это удалит все существующие данные в БД!"
    read -p "Продолжить? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        print_info "Операция отменена"
        exit 0
    fi
    
    # Распаковка
    if [[ "$DUMP_FILE" == *.gz ]]; then
        print_info "Распаковка дампа..."
        gunzip -c "$DUMP_FILE" > "${DUMP_FILE%.gz}"
        SQL_FILE="${DUMP_FILE%.gz}"
    else
        SQL_FILE="$DUMP_FILE"
    fi
    
    print_info "Импорт базы данных..."
    
    # Определяем имя БД из дампа (ищем CREATE DATABASE в первых строках)
    DB_NAME_FROM_DUMP=$(head -50 "$SQL_FILE" | grep -i "CREATE DATABASE" | sed -E "s/.*CREATE DATABASE[[:space:]]+([^;[:space:]]+).*/\1/i" | tr -d ';' | head -1)
    
    if [ -z "$DB_NAME_FROM_DUMP" ]; then
        # Если не нашли, пробуем найти в комментариях или используем значение из docker-compose
        DB_NAME_FROM_DUMP="crs_vpn"
        print_warning "Не удалось определить имя БД из дампа, используем: $DB_NAME_FROM_DUMP"
    else
        print_info "Найдено имя БД в дампе: $DB_NAME_FROM_DUMP"
    fi
    
    # Удаляем старую БД (пробуем оба варианта)
    docker exec -i "$DB_CONTAINER" psql -U crs_user -d postgres \
        -c "DROP DATABASE IF EXISTS crs_vpn;" 2>&1 | grep -v "does not exist" || true
    docker exec -i "$DB_CONTAINER" psql -U crs_user -d postgres \
        -c "DROP DATABASE IF EXISTS crs_vpn_bot;" 2>&1 | grep -v "does not exist" || true
    if [ -n "$DB_NAME_FROM_DUMP" ] && [ "$DB_NAME_FROM_DUMP" != "crs_vpn" ] && [ "$DB_NAME_FROM_DUMP" != "crs_vpn_bot" ]; then
        docker exec -i "$DB_CONTAINER" psql -U crs_user -d postgres \
            -c "DROP DATABASE IF EXISTS $DB_NAME_FROM_DUMP;" 2>&1 | grep -v "does not exist" || true
    fi
    
    # Импортируем дамп (с заменой имени БД если нужно)
    # Если в дампе crs_vpn_bot, а нужна crs_vpn, заменяем
    IMPORT_FILE="$SQL_FILE"
    if [ "$DB_NAME_FROM_DUMP" = "crs_vpn_bot" ]; then
        print_info "Переименование БД в дампе: crs_vpn_bot -> crs_vpn"
        IMPORT_FILE="${SQL_FILE}.renamed"
        sed 's/crs_vpn_bot/crs_vpn/g' "$SQL_FILE" > "$IMPORT_FILE"
    fi
    
    # Импортируем дамп
    print_info "Импорт данных (это может занять некоторое время)..."
    if ! docker exec -i "$DB_CONTAINER" psql -U crs_user -d postgres < "$IMPORT_FILE" 2>&1 | grep -v "WARNING" | grep -v "does not exist" | grep -v "already exists"; then
        print_warning "Импорт завершен (некоторые предупреждения могли быть проигнорированы)"
    fi
    
    # Удаляем временный файл если был создан
    if [ "$IMPORT_FILE" != "$SQL_FILE" ] && [ -f "$IMPORT_FILE" ]; then
        rm -f "$IMPORT_FILE"
    fi
    
    # Очистка
    if [[ "$DUMP_FILE" == *.gz ]]; then
        rm -f "$SQL_FILE"
    fi
    
    print_success "База данных успешно импортирована через Docker!"
    
    print_info "Следующие шаги:"
    echo "  1. Проверьте подключение: $DOCKER_COMPOSE_CMD exec bot python3 -m app.scripts.check_connections"
    echo "  2. Перезапустите контейнеры: $DOCKER_COMPOSE_CMD restart bot webhook-api"
}

# Главная функция
main() {
    case "${1:-}" in
        export)
            export_database
            ;;
        import)
            import_database "$2"
            ;;
        import-docker)
            import_database_docker "$2"
            ;;
        *)
            echo "Использование: $0 {export|import|import-docker} [dump_file]"
            echo ""
            echo "Команды:"
            echo "  export          - Экспорт БД с локального компьютера"
            echo "  import          - Импорт БД на сервер (через psql)"
            echo "  import-docker   - Импорт БД на сервер (через Docker)"
            echo ""
            echo "Примеры:"
            echo "  # Локально: экспорт БД"
            echo "  $0 export"
            echo ""
            echo "  # На сервере: импорт через Docker (рекомендуется)"
            echo "  $0 import-docker crs_vpn_dump_20240105_120000.sql.gz"
            echo ""
            echo "Переменные окружения для экспорта:"
            echo "  LOCAL_DB_HOST, LOCAL_DB_PORT, LOCAL_DB_NAME, LOCAL_DB_USER, LOCAL_DB_PASSWORD"
            echo ""
            echo "Переменные окружения для импорта:"
            echo "  SERVER_DB_HOST, SERVER_DB_PORT, SERVER_DB_NAME, SERVER_DB_USER, SERVER_DB_PASSWORD"
            exit 1
            ;;
    esac
}

main "$@"

