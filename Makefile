.PHONY: install verify smoke up down logs

VENV ?= .venv
PYTHON ?= python3

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install -r requirements-dev.txt

verify:
	$(VENV)/bin/python -m pytest tests/ -v

smoke:
	@test -x $(VENV)/bin/python || $(MAKE) install
	$(VENV)/bin/python scripts/smoke.py $(VENV)/bin/gunicorn

up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
