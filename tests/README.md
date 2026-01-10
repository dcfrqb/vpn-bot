# Руководство по тестированию

## Быстрый старт

Для запуска всех операций одной командой:

```bash
./scripts/run_all.sh all
```

Это выполнит:
1. Сборку Docker образов
2. Unit тесты
3. Интеграционные тесты
4. Запуск приложения (с подтверждением)

## Структура тестов

### Unit тесты

Unit тесты находятся в `tests/test_*.py` и тестируют отдельные функции и сервисы с использованием моков.

Примеры:
- `test_users.py` - тесты сервиса работы с пользователями
- `test_subscriptions.py` - тесты сервиса подписок
- `test_payments.py` - тесты сервиса платежей
- `test_stats.py` - тесты сервиса статистики
- `test_subscription_formatter.py` - тесты форматирования подписок

Запуск:
```bash
./scripts/run_all.sh test-unit
# или
make test-unit
# или
docker compose exec bot python3 -m pytest tests/ -v -m "not integration"
```

### Интеграционные тесты

Интеграционные тесты находятся в `tests/integration/` и тестируют полные пользовательские истории с реальной БД.

Покрываемые сценарии:
1. Регистрация пользователя (`test_user_registration_flow`)
   - Создание нового пользователя через `/start`
   - Автоматическое создание пробной подписки
   - Проверка сохранения в БД

2. Платеж и подписка (`test_payment_and_subscription_flow`)
   - Создание платежа через YooKassa
   - Обработка успешного платежа
   - Создание подписки после оплаты
   - Отправка уведомления пользователю

3. Истечение подписки (`test_subscription_expiration_flow`)
   - Проверка истечения подписки
   - Деактивация истекших подписок

4. Множественные подписки (`test_user_with_multiple_subscriptions`)
   - Работа с несколькими подписками пользователя
   - Выбор активной подписки

Запуск:
```bash
./scripts/run_all.sh test-integration
# или
make test-integration
```

Требования:
- Запущенная тестовая БД (автоматически запускается скриптом)
- Docker и Docker Compose

## Запуск через Docker

### Unit тесты

```bash
# Убедитесь, что основные сервисы запущены
docker compose up -d db redis

# Запустите тесты
docker compose exec bot python3 -m pytest tests/ -v -m "not integration"
```

### Интеграционные тесты

Тестовая БД создается автоматически и применяются миграции:

```bash
# Запуск тестовой БД и применение миграций
docker compose -f docker-compose.test.yml up -d

# Запуск интеграционных тестов
docker compose -f docker-compose.test.yml exec bot_test python3 -m pytest tests/integration/ -v -m "integration"

# Остановка тестовой БД
docker compose -f docker-compose.test.yml down
```

## Тестовая база данных

Для интеграционных тестов используется отдельная тестовая БД, которая создается автоматически при запуске тестов.

Параметры тестовой БД:
- База данных: `crs_vpn_test`
- Пользователь: `crs_user_test`
- Пароль: `crs_pass_test`
- Порт: `5433` (на хосте), `5432` (внутри Docker сети)
- Хост: `db_test` (внутри Docker), `localhost` (на хосте)

Тестовая БД создается автоматически при запуске интеграционных тестов через `./scripts/run_all.sh test-integration` или `make test-integration`. Миграции применяются автоматически при первом запуске.

Для ручного управления тестовой БД:

```bash
# Запуск тестовой БД
docker compose -f docker-compose.test.yml up -d db_test redis_test

# Применение миграций
docker compose -f docker-compose.test.yml up -d bot_test

# Остановка
docker compose -f docker-compose.test.yml down
```

Примечание: Тестовая БД использует tmpfs (в памяти), поэтому все данные удаляются при остановке контейнера. Это гарантирует чистоту тестов.

## Структура файлов

```
tests/
├── conftest.py                    # Конфигурация pytest и фикстуры
├── test_users.py                  # Unit тесты пользователей
├── test_subscriptions.py          # Unit тесты подписок
├── test_payments.py               # Unit тесты платежей
├── test_stats.py                  # Unit тесты статистики
├── test_subscription_formatter.py # Unit тесты форматирования
└── integration/
    └── test_user_flow.py          # Интеграционные тесты
```

## Фикстуры

### `test_db_session`
In-memory SQLite база для unit тестов. Автоматически создается и очищается для каждого теста.

### `test_db_with_postgres`
PostgreSQL база для интеграционных тестов. Использует тестовую БД из `docker-compose.test.yml`.

### `mock_bot`
Мок Telegram бота для тестов.

### `mock_remna_client`
Мок клиента Remna API для тестов.

## Требования к тестам

### Unit тесты
- Должны быть адекватными (не тривиальными)
- Используют моки для внешних зависимостей
- Быстро выполняются (не требуют БД)
- Изолированы друг от друга

### Интеграционные тесты
- Покрывают полные пользовательские истории
- Используют реальную БД (тестовую)
- Тестируют взаимодействие компонентов
- Очищают данные после выполнения

## Покрытие кода

Для проверки покрытия кода:

```bash
make test-coverage
# или
docker compose exec bot python3 -m pytest tests/ --cov=src/app --cov-report=html --cov-report=term -m "not integration"
```

Отчет будет доступен в `htmlcov/index.html`.

## Отладка тестов

### Запуск конкретного теста

```bash
docker compose exec bot python3 -m pytest tests/test_users.py::test_get_or_create_telegram_user_new -v
```

### Запуск с выводом print

```bash
docker compose exec bot python3 -m pytest tests/ -v -s
```

### Запуск с остановкой на первой ошибке

```bash
docker compose exec bot python3 -m pytest tests/ -v -x
```

## Troubleshooting

### Тестовая БД не запускается

```bash
# Проверьте, что порт 5433 свободен
lsof -i :5433

# Остановите все контейнеры
docker compose -f docker-compose.test.yml down

# Запустите заново
docker compose -f docker-compose.test.yml up -d
```

### Ошибки подключения к БД в интеграционных тестах

Убедитесь, что:
1. Тестовая БД запущена: `docker compose -f docker-compose.test.yml ps`
2. БД готова: `docker compose -f docker-compose.test.yml exec db_test pg_isready -U crs_user_test`
3. Переменная `TEST_DATABASE_URL` установлена правильно

### Тесты падают с ошибками импорта

Убедитесь, что зависимости установлены:
```bash
docker compose exec bot pip install -r requirements.txt
```
