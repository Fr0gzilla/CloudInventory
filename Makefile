.PHONY: install verify smoke up down logs

VENV ?= .venv
PYTHON ?= python3

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install -r requirements.txt

verify:
	$(VENV)/bin/python -m pytest tests/ -v

smoke:
	@test -x $(VENV)/bin/python || $(MAKE) install
	USE_MOCK_VIRT=true USE_MOCK_IPAM=true SESSION_COOKIE_SECURE=false sh scripts/start.sh $(VENV)/bin/python -c 'import os,socket; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError("outbound network forbidden")); socket.socket.connect_ex=socket.socket.connect; socket.create_connection=socket.socket.connect; from app import create_app,db; app=create_app(); client=app.test_client(); assert client.get("/login").status_code==200; csrf=client.session_transaction(); session=csrf.__enter__(); token_csrf=session["csrf_token"]; csrf.__exit__(None,None,None); assert client.post("/login",data={"username":"admin","password":os.environ["ADMIN_PASSWORD"],"csrf_token":token_csrf}).status_code==302; assert client.get("/health").status_code==200; response=client.post("/api/login",json={"username":"admin","password":os.environ["ADMIN_PASSWORD"]}); assert response.status_code==200,response.get_json(); token=response.get_json()["access_token"]; headers={"Authorization":"Bearer "+token}; app.app_context().push(); db.create_all(); result=client.post("/api/runs",headers=headers); assert result.status_code==201 and result.get_json()["status"]=="SUCCESS",result.get_json(); print("smoke: health, login, simulated run SUCCESS")'

up:
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
