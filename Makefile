.PHONY: install fmt lint type test audit check build tf-fmt

install:
	uv sync --all-groups
	uv run pre-commit install

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

type:
	uv run mypy

test:
	uv run pytest --cov --cov-report=term-missing

audit:
	uv run pip-audit --skip-editable

check: lint type test

build:
	./scripts/build_lambda.sh

tf-fmt:
	terraform fmt -recursive terraform
