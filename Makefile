.PHONY: install verify smoke up down logs

VENV ?= .venv
PYTHON ?= python3

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install -r requirements-dev.txt

verify:
	$(VENV)/bin/ruff check app collector
	$(VENV)/bin/mypy app collector
	$(VENV)/bin/coverage run -m pytest tests/ -q
	$(VENV)/bin/coverage report --fail-under=0
	$(VENV)/bin/bandit -r app collector -q
	$(VENV)/bin/pip-audit -r requirements.txt

smoke:
	@test -x $(VENV)/bin/python || $(MAKE) install
	$(VENV)/bin/python scripts/smoke.py $(VENV)/bin/gunicorn

up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
