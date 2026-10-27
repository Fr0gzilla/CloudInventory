from flask import jsonify, request
from flask_jwt_extended import create_access_token

from . import jwt


def configure_api(app, authenticate):
    @jwt.unauthorized_loader
    def missing_token(reason):
        return jsonify(error="Unauthorized"), 401

    @jwt.invalid_token_loader
    def invalid_token(reason):
        return jsonify(error="Unauthorized"), 401

    @jwt.expired_token_loader
    def expired_token(header, payload):
        return jsonify(error="Unauthorized"), 401

    @jwt.revoked_token_loader
    def revoked_token(header, payload):
        return jsonify(error="Unauthorized"), 401

    def login():
        """Authenticate an administrator.
        ---
        tags:
          - Authentication
        security: []
        consumes:
          - application/json
        parameters:
          - in: body
            name: credentials
            required: true
            schema:
              type: object
              required: [username, password]
              properties:
                username:
                  type: string
                password:
                  type: string
                  format: password
        responses:
          200:
            description: Bearer access token
            schema:
              type: object
              properties:
                access_token:
                  type: string
          401:
            description: Invalid credentials
          429:
            description: Too many attempts
        """
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Invalid credentials"), 400
        username = data.get("username")
        password = data.get("password")
        if (not isinstance(username, str) or not username or len(username) > 128 or
                not isinstance(password, str) or not password or len(password) > 4096):
            return jsonify(error="Invalid credentials"), 400
        result = authenticate(username, password)
        if result == "limited":
            return jsonify(error="Too many attempts"), 429
        if result == "invalid":
            return jsonify(error="Invalid credentials"), 401
        return jsonify(access_token=create_access_token(identity="admin"))

    app.add_url_rule("/api/login", endpoint="api.login", view_func=login, methods=["POST"])
