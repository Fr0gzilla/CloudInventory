.PHONY: install verify smoke

VENV ?= .venv
PYTHON ?= python3

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install -r requirements.txt

verify:
	$(VENV)/bin/python -m pytest tests/ -v

smoke:
	$(VENV)/bin/python -m pytest tests/test_app.py::test_smoke -v
