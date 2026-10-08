VENV := venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install test verify smoke up down logs

install:
	python3 -m venv $(VENV)
	$(PIP) install -r requirements.txt

# --ignore=reference : code source de référence en lecture seule, hors suite de tests
# code 5 (aucun test collecté) toléré tant que tests/ n'existe pas
test:
	$(PY) -m pytest -v --ignore=reference || [ $$? -eq 5 ]

smoke: up
	@bash scripts/smoke.sh
	@$(MAKE) down

up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
