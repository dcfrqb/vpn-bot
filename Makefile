.PHONY: up down logs migrate migrate-create fmt test install

# Docker Compose команды
up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

# Миграции базы данных (one-shot, требует db)
migrate:
	docker compose --profile migrate run --rm migrate

migrate-create:
	docker compose --profile migrate run --rm migrate sh -c 'python3 -m alembic revision --autogenerate -m "auto"'

# Установка зависимостей (для локальной разработки)
install:
	pip install -r requirements.txt

# Форматирование кода
fmt:
	python3 -m pip install ruff black --quiet || true
	ruff check --fix .
	black .

# Тестирование (Docker)
test:
	docker compose exec bot python3 -m pytest tests/ -v -m "not integration"

test-unit:
	docker compose exec bot python3 -m pytest tests/ -v -m "not integration"

test-integration:
	@echo "Запуск тестовой БД..."
	docker compose -f docker-compose.test.yml up -d db_test redis_test
	@sleep 5
	@echo "Применение миграций к тестовой БД..."
	docker compose -f docker-compose.test.yml up -d bot_test
	@sleep 3
	@echo "Запуск интеграционных тестов..."
	docker compose -f docker-compose.test.yml exec -e TEST_DATABASE_URL="postgresql+asyncpg://crs_user_test:crs_pass_test@db_test:5432/crs_vpn_test" bot_test python3 -m pytest tests/integration/ -v -m "integration"
	@echo "Остановка тестовой БД..."
	docker compose -f docker-compose.test.yml down

test-all: test-unit test-integration

test-coverage:
	docker compose exec bot python3 -m pytest tests/ --cov=src/app --cov-report=html --cov-report=term -m "not integration"

# Локальное тестирование (без Docker)
test-local:
	PYTHONPATH=src python3 -m pytest tests/ -v -m "not integration"

test-unit-local:
	PYTHONPATH=src python3 -m pytest tests/ -v -m "not integration"

test-coverage-local:
	PYTHONPATH=src python3 -m pytest tests/ --cov=src/app --cov-report=html --cov-report=term -m "not integration"

# Локальный запуск (для разработки, без Docker)
run:
	PYTHONPATH=src python3 -m app.main