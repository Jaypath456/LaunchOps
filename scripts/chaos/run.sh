#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"
export HIRES=${HIRES:-100} REPLICAS=${REPLICAS:-3} KILL_EVERY=${KILL_EVERY:-10}
export TIMEOUT=${TIMEOUT:-900} ERROR_RATE=${ERROR_RATE:-0.2} HANG_RATE=${HANG_RATE:-0.05}
export DROP_RATE=${DROP_RATE:-0.1} RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
export PAUSE_EVERY=${PAUSE_EVERY:-0} LATENCY_MS=${LATENCY_MS:-0} LEASE=${LEASE:-30s}
if [[ ! "$RUN_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
  echo 'RUN_ID must contain only letters, digits, underscores or hyphens.' >&2
  exit 1
fi
export CHAOS_EVIDENCE=${CHAOS_EVIDENCE:-"$ROOT/docs/evidence/chaos/$RUN_ID"}
# Volumes survive teardown; a repeated evidence label still needs fresh database state.
export CHAOS_VOLUME="launchops-chaos-$RUN_ID-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
export CHAOS_SCRATCH
CHAOS_SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/launchops-chaos.XXXXXX")
# Buildx writes activity metadata even for local builds; keep it in writable scratch space.
export BUILDX_CONFIG="$CHAOS_SCRATCH/buildx"
python3 - <<'PY'
import json
import math
import os
from pathlib import Path
import re
import shlex

config = {key.lower(): int(os.environ[key]) for key in ('HIRES', 'REPLICAS')}
for key in ('KILL_EVERY', 'PAUSE_EVERY', 'TIMEOUT', 'ERROR_RATE', 'HANG_RATE', 'DROP_RATE'):
    config[key.lower()] = float(os.environ[key])
lease = os.environ['LEASE']
parts = re.findall(r'(\d+(?:\.\d*)?|\.\d+)(ns|us|µs|μs|ms|s|m|h)', lease)
assert parts and ''.join(n + unit for n, unit in parts) == lease, 'LEASE must be a positive Go duration'
units = {'ns': 1e-9, 'us': 1e-6, 'µs': 1e-6, 'μs': 1e-6, 'ms': .001, 's': 1, 'm': 60, 'h': 3600}
lease_seconds = sum(float(n) * units[unit] for n, unit in parts)
assert lease_seconds > 0, 'LEASE must be positive'
config['pause_for'] = float(os.environ.get('PAUSE_FOR') or lease_seconds + 5)
config['latency_ms'] = int(os.environ['LATENCY_MS'])
assert config['hires'] > 0 and config['replicas'] > 0, 'HIRES and REPLICAS must be positive'
assert all(math.isfinite(value) for value in config.values()), 'Parameters must be finite'
assert config['kill_every'] >= 0 and config['timeout'] > 0, 'Invalid kill interval or timeout'
assert config['pause_every'] >= 0 and config['pause_for'] > 0, 'Invalid pause interval or duration'
assert 0 <= config['latency_ms'] <= 10000, 'LATENCY_MS must be between 0 and 10000'
assert all(0 <= config[key] <= 1 for key in ('error_rate', 'hang_rate', 'drop_rate')), 'Invalid fault rate'
config.update(run_id=os.environ['RUN_ID'], project='launchops-chaos', mongo_volume=os.environ['CHAOS_VOLUME'], lease=lease)
root = Path(os.environ['CHAOS_EVIDENCE'])
root.mkdir()  # Never overwrite evidence from another run.
(root / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
os.environ['PAUSE_FOR'] = str(config['pause_for'])
keys = ('HIRES', 'REPLICAS', 'KILL_EVERY', 'PAUSE_EVERY', 'PAUSE_FOR', 'LATENCY_MS', 'LEASE',
        'TIMEOUT', 'ERROR_RATE', 'HANG_RATE', 'DROP_RATE', 'RUN_ID', 'CHAOS_EVIDENCE')
command = ['env', *(f'{key}={os.environ[key]}' for key in keys), 'bash', 'scripts/chaos/run.sh']
(root / 'COMMAND.txt').write_text(shlex.join(command) + '\n')
(Path(os.environ['CHAOS_SCRATCH']) / 'compose.yaml').write_text('''services:
  mongo:
    ports: !reset []
  backend:
    ports: !reset []
    environment:
      DEBUG: "True"
      MONGO_DB: launchops
  mock-saas:
    ports: !reset []
    environment:
      ERROR_RATE: "0"
      HANG_RATE: "0"
      DROP_AFTER_COMMIT_RATE: "0"
      LATENCY_MS: "0"
  provisioner:
    environment:
      LEASE: ''' + json.dumps(lease) + '''
volumes:
  mongo_data:
    name: ''' + json.dumps(config['mongo_volume']) + '\n')
PY
export PAUSE_FOR
PAUSE_FOR=$(python3 -c 'import json,os; print(json.load(open(os.environ["CHAOS_EVIDENCE"] + "/config.json"))["pause_for"])')

compose=(docker compose --env-file /dev/null -p launchops-chaos -f "$ROOT/docker-compose.yml" -f "$CHAOS_SCRATCH/compose.yaml")
owned=false
killer_pid=
pauser_pid=
client() {
  "${compose[@]}" exec -T backend python - "$@" < "$ROOT/scripts/chaos/client.py"
}
stop_faults() {
  touch "$CHAOS_SCRATCH/stop"
  if [[ -n "$killer_pid" ]]; then
    kill "$killer_pid" 2>/dev/null || true
    wait "$killer_pid" 2>/dev/null || true
    killer_pid=
  fi
  if [[ -n "$pauser_pid" ]]; then
    kill "$pauser_pid" 2>/dev/null || true
    wait "$pauser_pid" || return 1
    pauser_pid=
  fi
}
unpause_all() {
  python3 "$ROOT/scripts/chaos/pause.py" "$CHAOS_EVIDENCE/pauses.jsonl" --cleanup
}
cleanup() {
  result=$?
  trap - EXIT
  trap '' INT TERM  # Repeated signals must not interrupt unpause or teardown.
  set +e  # Evidence and teardown must still run after an earlier command fails.
  stop_faults || result=1
  if [[ "$owned" == true ]]; then
    unpause_all || result=1
    # Drain outstanding finishes before taking the snapshot and reading simulator counters.
    "${compose[@]}" stop provisioner || result=1
    "${compose[@]}" logs --no-color --no-log-prefix provisioner > "$CHAOS_EVIDENCE/provisioner.log" 2>&1 || result=1
    client snapshot > "$CHAOS_EVIDENCE/jobs.json" || result=1
    client accounts > "$CHAOS_EVIDENCE/mocksaas-accounts.json" || result=1
    client stats > "$CHAOS_EVIDENCE/mocksaas-stats.json" || result=1
    {
      uname -a
      if command -v sysctl >/dev/null; then sysctl -n machdep.cpu.brand_string hw.ncpu; else cat /proc/cpuinfo; fi
      "${compose[@]}" exec -T backend python -c 'import os,platform; print("Container CPU:", platform.machine(), "logical CPUs:", os.cpu_count())'
      docker version
      docker compose version
      "${compose[@]}" exec -T mongo mongosh --quiet --eval 'db.version()'
      python3 --version
      "${compose[@]}" exec -T backend python --version
      if command -v go >/dev/null; then go version; else echo 'Go toolchain unavailable on host; see Docker version for build toolchain'; fi
    } > "$CHAOS_EVIDENCE/ENV.txt" 2>&1
  fi
  verifier=(python3 "$ROOT/scripts/chaos/verify.py" "$CHAOS_EVIDENCE")
  if python3 -c 'import os,sys; sys.exit(0 if float(os.environ["KILL_EVERY"]) == float(os.environ["PAUSE_EVERY"]) == 0 else 1)'; then
    verifier+=(--dry-run)
  fi
  "${verifier[@]}" || result=1
  if [[ "$owned" == true ]]; then
    unpause_all || result=1
    "${compose[@]}" down || result=1
  fi
  rm -rf "$CHAOS_SCRATCH"
  echo "Evidence: $CHAOS_EVIDENCE"
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# This project is exclusive; preserve every existing volume and refuse to reuse campaign state.
docker info > /dev/null
if [[ -n "$(docker ps -aq --filter label=com.docker.compose.project=launchops-chaos)" ]]; then
  echo 'launchops-chaos already has containers; refusing to disturb them.' >&2
  exit 1
fi
if docker volume inspect "$CHAOS_VOLUME" > /dev/null 2>&1; then
  echo 'Generated campaign volume already exists; refusing to reuse state.' >&2
  exit 1
fi
: > "$CHAOS_EVIDENCE/kills.jsonl"
: > "$CHAOS_EVIDENCE/pauses.jsonl"
owned=true
"${compose[@]}" up -d --build --scale "provisioner=$REPLICAS" --wait --wait-timeout 180 mongo mock-saas backend provisioner 2>&1 | tee "$CHAOS_EVIDENCE/startup.log"
client faults --error-rate "$ERROR_RATE" --hang-rate "$HANG_RATE" --drop-rate "$DROP_RATE" --latency-ms "$LATENCY_MS" > "$CHAOS_EVIDENCE/faults.json"
"${compose[@]}" exec -T backend python manage.py seed_launchops --bulk-employees "$HIRES"

if python3 -c 'import os,sys; sys.exit(0 if float(os.environ["KILL_EVERY"]) > 0 else 1)'; then
  (
    while sleep "$KILL_EVERY"; do
      [[ ! -e "$CHAOS_SCRATCH/stop" ]] || break
      "${compose[@]}" ps -q provisioner > "$CHAOS_SCRATCH/containers"
      container=$(python3 -c 'import random,sys; ids=sys.stdin.read().split(); print(random.choice(ids) if ids else "")' < "$CHAOS_SCRATCH/containers")
      if [[ -z "$container" ]]; then
        echo 'No provisioner container available to kill.' >&2
        break
      fi
      if docker kill "$container" > /dev/null; then
        python3 - "$container" >> "$CHAOS_EVIDENCE/kills.jsonl" <<'PY'
from datetime import UTC, datetime
import json
import sys
print(json.dumps({'container': sys.argv[1], 'at': datetime.now(UTC).isoformat()}))
PY
        docker start "$container" > /dev/null || break
      else
        break
      fi
    done
  ) &
  killer_pid=$!
fi
if python3 -c 'import os,sys; sys.exit(0 if float(os.environ["PAUSE_EVERY"]) > 0 else 1)'; then
  python3 "$ROOT/scripts/chaos/pause.py" "$CHAOS_EVIDENCE/pauses.jsonl" \
    --stop-file "$CHAOS_SCRATCH/stop" --every "$PAUSE_EVERY" --duration "$PAUSE_FOR" &
  pauser_pid=$!
fi
client create --hires "$HIRES" > "$CHAOS_EVIDENCE/created.json"
client wait --hires "$HIRES" --timeout "$TIMEOUT" > "$CHAOS_SCRATCH/wait.json"
