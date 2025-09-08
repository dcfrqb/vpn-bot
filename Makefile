.PHONY: run up down migrate alembic-init fmt

run:
\tpython -m src.app.main

up:
\tdocker compose up -d

down:
\tdocker compose down

alembic-init:
\talembic init -t async src/app/db/migrations

migrate:
\talebmic revision -m "auto" --autogenerate && alembic upgrade head

fmt:
\tpython -m pip install ruff black --quiet || true
\truff check --fix .
\tblack .