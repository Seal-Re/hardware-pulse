#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

if [ -z "${PREFIX:-}" ]; then
  echo "ERROR: PREFIX is not set. This script is intended to run inside Termux." >&2
  exit 1
fi

export PGDATA="$PREFIX/var/lib/postgresql"
export PGHOST="127.0.0.1"
export PGPORT="5432"

LOG_DIR="$PREFIX/var/log"
RUN_DIR="$PREFIX/var/run/postgresql"
mkdir -p "$LOG_DIR" "$RUN_DIR"

dotenv_get() {
  local key="$1" file="$2"
  [ -f "$file" ] || return 1
  local line
  line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
  [ -n "$line" ] || return 1
  line="${line#*=}"
  line="${line%$'\r'}"
  if [ "${line:0:1}" = '"' ] && [ "${line: -1}" = '"' ]; then
    line="${line:1:${#line}-2}"
  elif [ "${line:0:1}" = "'" ] && [ "${line: -1}" = "'" ]; then
    line="${line:1:${#line}-2}"
  fi
  printf '%s' "$line"
}

parse_jdbc() {
  local url="$1"
  url="${url#jdbc:postgresql://}"
  local hostport="${url%%/*}"
  local dbname="${url##*/}"
  dbname="${dbname%%\?*}"

  local host="$hostport" port="5432"
  if echo "$hostport" | grep -q ':'; then
    host="${hostport%%:*}"
    port="${hostport##*:}"
  fi
  printf '%s %s %s' "$host" "$port" "$dbname"
}

ENV_FILE=".env"
JDBC_URL=""
DB_USER=""
DB_PASS=""
DB_NAME=""

if [ -f "$ENV_FILE" ]; then
  JDBC_URL="$(dotenv_get SPRING_DATASOURCE_URL "$ENV_FILE" || true)"
  DB_USER="$(dotenv_get SPRING_DATASOURCE_USERNAME "$ENV_FILE" || true)"
  DB_PASS="$(dotenv_get SPRING_DATASOURCE_PASSWORD "$ENV_FILE" || true)"
  [ -n "$DB_USER" ] || DB_USER="$(dotenv_get POSTGRES_USER "$ENV_FILE" || true)"
  [ -n "$DB_PASS" ] || DB_PASS="$(dotenv_get POSTGRES_PASSWORD "$ENV_FILE" || true)"
fi

if [ -z "$JDBC_URL" ]; then
  JDBC_URL="jdbc:postgresql://127.0.0.1:5432/hardware_pulse"
fi

read -r _host _port _db <<<"$(parse_jdbc "$JDBC_URL")"
DB_NAME="${_db:-hardware_pulse}"

[ -n "$DB_USER" ] || DB_USER="hardware_pulse"
[ -n "$DB_PASS" ] || DB_PASS="hardware_pulse"

echo "[1/4] Installing packages (non-interactive)..."
pkg update -y >/dev/null 2>&1 || true
pkg install -y postgresql redis openjdk-17 python >/dev/null

echo "[2/4] Initializing PostgreSQL data dir if needed..."
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  mkdir -p "$PGDATA"
  initdb -D "$PGDATA" >/dev/null
fi

ensure_pg_conf() {
  local conf="$1"
  [ -f "$conf" ] || return 0

  if grep -Eq '^\s*#?\s*listen_addresses\s*=' "$conf"; then
    sed -i -E "s/^\s*#?\s*listen_addresses\s*=.*/listen_addresses = '127.0.0.1'/" "$conf"
  else
    printf "\nlisten_addresses = '127.0.0.1'\n" >>"$conf"
  fi

  if grep -Eq '^\s*#?\s*synchronous_commit\s*=' "$conf"; then
    sed -i -E 's/^\s*#?\s*synchronous_commit\s*=.*/synchronous_commit = off/' "$conf"
  else
    printf "\nsynchronous_commit = off\n" >>"$conf"
  fi

  if grep -Eq '^\s*#?\s*shared_buffers\s*=' "$conf"; then
    sed -i -E 's/^\s*#?\s*shared_buffers\s*=.*/shared_buffers = 256MB/' "$conf"
  else
    printf "\nshared_buffers = 256MB\n" >>"$conf"
  fi

  if grep -Eq '^\s*#?\s*max_connections\s*=' "$conf"; then
    sed -i -E 's/^\s*#?\s*max_connections\s*=.*/max_connections = 50/' "$conf"
  else
    printf "\nmax_connections = 50\n" >>"$conf"
  fi
}

echo "[3/4] Applying Termux flash-friendly PostgreSQL settings..."
ensure_pg_conf "$PGDATA/postgresql.conf"

echo "[4/4] Bootstrapping roles/db/schema..."
SETUP_PG_LOG="$LOG_DIR/postgresql-setup.log"

pg_ctl -D "$PGDATA" -l "$SETUP_PG_LOG" -o "-p $PGPORT -h 127.0.0.1" start >/dev/null

wait_pg_ready() {
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if pg_isready -h "$PGHOST" -p "$PGPORT" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

if ! wait_pg_ready; then
  echo "ERROR: PostgreSQL did not become ready. See $SETUP_PG_LOG" >&2
  exit 1
fi

USER_EXISTS=$(psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")
if [ "$USER_EXISTS" != "1" ]; then
    createuser "$DB_USER"
fi
psql postgres -c "ALTER USER \"$DB_USER\" WITH PASSWORD '$DB_PASS';" >/dev/null

DB_EXISTS=$(psql postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")
if [ "$DB_EXISTS" != "1" ]; then
    createdb -O "$DB_USER" "$DB_NAME"
fi

if [ -f "init-scripts/01_schema.sql" ]; then
  psql -v ON_ERROR_STOP=1 -h "$PGHOST" -p "$PGPORT" "$DB_NAME" -f "init-scripts/01_schema.sql" >/dev/null
fi

pg_ctl -D "$PGDATA" stop -m fast >/dev/null

echo "OK"
echo "- DB_NAME=$DB_NAME"
echo "- DB_USER=$DB_USER"
echo "- DB_PASSWORD=(hidden)"