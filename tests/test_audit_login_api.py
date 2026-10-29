import os

from flask import g

from app import db
from app.audit import record_action
from app.models import Audit


def test_api_login_without_verified_jwt_records_system_actor(orm_app):
    client = orm_app.test_client()
    before = Audit.query.count()

    response = client.post('/api/login', json={
        'username': 'admin', 'password': os.environ['ADMIN_PASSWORD'],
    })

    assert response.status_code == 200
    assert isinstance(response.get_json().get('access_token'), str)
    with orm_app.test_request_context('/api/login', method='POST'):
        assert getattr(g, '_jwt_extended_jwt', None) is None
        with db.engine.begin() as connection:
            record_action(connection, 'login sans JWT', 'api:login')
    traces = Audit.query.order_by(Audit.id).all()[before:]
    assert [(trace.action, trace.actor_identity) for trace in traces] == [
        ('login sans JWT', 'système'),
    ]


def test_api_login_with_invalid_password_does_not_record_action(orm_app):
    before = Audit.query.count()

    response = orm_app.test_client().post('/api/login', json={
        'username': 'admin', 'password': 'invalid-password',
    })

    assert response.status_code == 401
    assert Audit.query.count() == before
