#!/bin/sh
set -eu

for key in SECRET_KEY JWT_SECRET_KEY ADMIN_PASSWORD; do
  eval "value=\${$key-}"
  if [ -z "$value" ] || [ "$value" = change-me ]; then
    value=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
    export "$key=$value"
  fi
done

exec "$@"
