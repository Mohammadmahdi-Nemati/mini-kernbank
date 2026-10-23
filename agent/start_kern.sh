# Gemeinsamer Teil von start.command – nicht direkt ausführen.
# Erwartet: DSN_VORLAGE (mit {PORT}) und optional die Funktion vor_db_start.
AGENT="$PWD"; REPO="$(cd .. && pwd)"
[ -x .venv/bin/python ] || { echo "Zuerst agent/einrichten.command ausführen."; exit 1; }

export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin"
PORT=""
if command -v docker >/dev/null; then
  if ! docker info >/dev/null 2>&1 && command -v open >/dev/null; then
    echo "Docker startet ..."; open -a Docker
    for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
  fi
  if docker info >/dev/null 2>&1; then
    cd "$REPO" || exit 1
    if [ -n "$(docker compose ps -q db 2>/dev/null)" ]; then
      PORT=$(docker compose port db 5432 2>/dev/null | sed 's/.*://')
      echo "Datenbank läuft bereits (Port $PORT)."
    else
      declare -F vor_db_start >/dev/null && vor_db_start
      if ! (exec 3<>/dev/tcp/127.0.0.1/5432) 2>/dev/null; then
        docker compose up -d >/dev/null 2>&1 && PORT=5432
      else
        echo "Port 5432 ist belegt (vermutlich die Datenbank eines anderen Projekts) – nutze 5433."
        docker compose -f docker-compose.yml -f "$AGENT/docker-port-5433.yml" up -d >/dev/null 2>&1 && PORT=5433
      fi
      [ -n "$PORT" ] && echo "Datenbank gestartet (Port $PORT)." \
                     || echo "Hinweis: Datenbank konnte nicht starten – der Agent läuft trotzdem, nur ohne SQL."
    fi
    cd "$AGENT" || exit 1
  fi
else
  echo "Hinweis: Docker nicht gefunden – der Agent läuft ohne Datenbank."
fi
[ -n "$PORT" ] && export AGENT_DB_DSN="${DSN_VORLAGE//\{PORT\}/$PORT}"

exec "$AGENT/.venv/bin/python" "$AGENT/app.py" "$@"
