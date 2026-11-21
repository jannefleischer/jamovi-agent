#!/bin/sh
# Install the working tree into a running jamovi test container, restart
# jamovi and run the end-to-end smoke test inside it.
#
#   scripts/smoke.sh [container] [extra smoke.py args, e.g. --chat "..."]
#
# The container runs the jamovi image with `jupyter server` instead of
# jupyterhub-singleuser (token "t", base url /user/test/), see the stack's
# CLAUDE.md "Test a standalone image without the hub".
set -e
C="${1:-jamovi-test}"
[ $# -gt 0 ] && shift
cd "$(dirname "$0")/.."
SRV=/usr/lib/jamovi/server
BASE=http://127.0.0.1:8888/user/test/jamovi

docker exec "$C" rm -rf "$SRV/jamovi_agent"
docker cp src/jamovi_agent "$C:$SRV/"
docker cp shim/extras.py "$C:$SRV/jamovi/server/extras.py"
docker cp scripts/smoke.py "$C:/tmp/smoke.py"

# jupyter-server-proxy does not reliably restart a killed jamovi: restart the
# container instead (copied files survive a restart)
docker restart "$C" >/dev/null
docker exec "$C" sh -c "for i in \$(seq 1 30); do
    curl -sf -o /dev/null -H 'Authorization: token t' http://127.0.0.1:8888/user/test/api/status && break
    sleep 1
  done
  curl -s -o /dev/null -H 'Authorization: token t' $BASE/
  for i in \$(seq 1 60); do
    curl -sf -o /dev/null -H 'Authorization: token t' $BASE/agent/health && break
    sleep 1
  done"
docker exec -w /tmp "$C" env PYTHONPATH="$SRV" python3 /tmp/smoke.py "$BASE" --token t "$@"
