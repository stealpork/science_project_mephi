#!/bin/sh
set -eu
for service in identity people projects publications media activity; do
    upper=$(printf '%s' "$service" | tr '[:lower:]' '[:upper:]')
    password=$(printenv "${upper}_DATABASE_PASSWORD")
    psql --username "$POSTGRES_USER" --dbname postgres --set=service="$service" --set=password="$password" --set=ON_ERROR_STOP=1 <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'service', :'password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'service', :'service') \gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'service') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'service', :'service') \gexec
SQL
done
