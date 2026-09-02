#!/usr/bin/env bash
#
# Test harness for runner_watchdog.sh.
#
# Why this exists: the watchdog installed on 2026-08-23 was "tested" with
#
#     WATCHDOG_DRY_RUN=1 WATCHDOG_PORT=59999 ./tools/runner_watchdog.sh
#
# Port 59999 has nothing listening. But in production port 4912 is held by
# `docker-proxy` for the container's ENTIRE lifetime, whether or not anything
# inside the container is alive. So the real failure looks like this:
#
#     TCP connect succeeds  ->  watchdog says "healthy"
#     nothing serves HTTP   ->  the runner is actually crash-looping
#
# The old test never produced that state, so it passed while the watchdog was
# blind. On 2026-09-02 the runner crash-looped for 4.9 hours and the watchdog
# log stayed empty.
#
# The fixtures below reproduce it exactly, with no board and no downtime:
#
#   healthy     python3 http server   TCP accepts + answers HTTP
#   proxyonly   accept-then-close     TCP accepts + NO HTTP   <-- docker-proxy
#   dead        nothing listening     TCP refused
#
# Any probe that cannot tell `healthy` from `proxyonly` is not a liveness check.
#
# Usage:
#   ./tools/test_runner_watchdog.sh                       # tests runner_watchdog.sh
#   WATCHDOG_SCRIPT=./tools/other.sh ./tools/test_runner_watchdog.sh

set -u

SCRIPT="${WATCHDOG_SCRIPT:-$(dirname "$0")/runner_watchdog.sh}"
if [ ! -f "$SCRIPT" ]; then
    echo "FATAL: script under test not found: $SCRIPT"
    exit 2
fi
SCRIPT=$(cd "$(dirname "$SCRIPT")" && pwd)/$(basename "$SCRIPT")

SANDBOX=$(mktemp -d)
BIN="$SANDBOX/bin"
STATE="$SANDBOX/state"
mkdir -p "$BIN" "$STATE"

PASS=0
FAIL=0
FIXTURE_PID=""
FIXTURE_PORT=45912

stop_fixture() {
    if [ -n "$FIXTURE_PID" ]; then
        kill "$FIXTURE_PID" 2>/dev/null
        wait "$FIXTURE_PID" 2>/dev/null
        FIXTURE_PID=""
    fi
}

cleanup() {
    stop_fixture
    rm -rf "$SANDBOX"
}
trap cleanup EXIT

# ---------------------------------------------------------------- fake docker
# Records what the watchdog asked for; never touches a real container.
{
    echo '#!/usr/bin/env bash'
    echo 'SB="$FAKE_DOCKER_SANDBOX"'
    echo 'echo "$*" >> "$SB/docker_calls"'
    echo 'case "$1" in'
    echo '    inspect)'
    echo '        cat "$SB/container_running" 2>/dev/null || echo true'
    echo '        ;;'
    echo '    restart)'
    echo '        echo "$*" >> "$SB/restart_calls"'
    echo '        exit "$(cat "$SB/restart_exit" 2>/dev/null || echo 0)"'
    echo '        ;;'
    echo '    exec)'
    echo '        cat "$SB/exec_output" 2>/dev/null'
    echo '        exit "$(cat "$SB/exec_exit" 2>/dev/null || echo 0)"'
    echo '        ;;'
    echo 'esac'
    echo 'exit 0'
} > "$BIN/docker"
chmod +x "$BIN/docker"

echo "true" > "$SANDBOX/container_running"

# --------------------------------------------------------------- port fixtures
wait_for_port() {
    local i
    for i in $(seq 1 40); do
        if timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/${FIXTURE_PORT}" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

wait_for_no_port() {
    local i
    for i in $(seq 1 40); do
        if ! timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/${FIXTURE_PORT}" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

# A real HTTP server: TCP accepts AND answers HTTP. This is a live runner.
start_healthy() {
    stop_fixture
    python3 -m http.server "$FIXTURE_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
    FIXTURE_PID=$!
    wait_for_port
}

# docker-proxy with a dead upstream: accepts the connection, then closes it
# without speaking HTTP. This is the crash-looping runner.
start_proxyonly() {
    stop_fixture
    python3 "$SANDBOX/proxyonly.py" "$FIXTURE_PORT" >/dev/null 2>&1 &
    FIXTURE_PID=$!
    wait_for_port
}

# Nothing listening at all.
start_dead() {
    stop_fixture
    wait_for_no_port
}

{
    echo 'import socket, sys'
    echo 's = socket.socket()'
    echo 's.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)'
    echo 's.bind(("127.0.0.1", int(sys.argv[1])))'
    echo 's.listen(8)'
    echo 'while True:'
    echo '    try:'
    echo '        c, _ = s.accept()'
    echo '        c.close()'
    echo '    except Exception:'
    echo '        break'
} > "$SANDBOX/proxyonly.py"

# ------------------------------------------------------------------- test glue
run_watchdog() {
    env PATH="$BIN:$PATH" \
        FAKE_DOCKER_SANDBOX="$SANDBOX" \
        WATCHDOG_STATE_DIR="$STATE" \
        WATCHDOG_HOST=127.0.0.1 \
        WATCHDOG_PORT="$FIXTURE_PORT" \
        WATCHDOG_CONTAINER=fake-runner \
        HOME="$SANDBOX" \
        "$SCRIPT" >/dev/null 2>&1
}

reset_state() {
    rm -rf "$STATE"
    mkdir -p "$STATE"
    rm -f "$SANDBOX/restart_calls" "$SANDBOX/docker_calls"
    echo "true" > "$SANDBOX/container_running"
}

log_tail() { tail -n 1 "$STATE/watchdog.log" 2>/dev/null; }
log_lines() { if [ -f "$STATE/watchdog.log" ]; then wc -l < "$STATE/watchdog.log" | tr -d ' '; else echo 0; fi; }
restart_count() { if [ -f "$SANDBOX/restart_calls" ]; then wc -l < "$SANDBOX/restart_calls" | tr -d ' '; else echo 0; fi; }
fail_count() { cat "$STATE/consecutive_failures" 2>/dev/null || echo 0; }

check() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        printf '  PASS  %s\n' "$name"
        PASS=$((PASS + 1))
    else
        printf '  FAIL  %s\n          expected: %s\n          actual:   %s\n' \
            "$name" "$expected" "$actual"
        FAIL=$((FAIL + 1))
    fi
}

check_contains() {
    local name="$1" needle="$2" hay="$3"
    case "$hay" in
        *"$needle"*)
            printf '  PASS  %s\n' "$name"
            PASS=$((PASS + 1)) ;;
        *)
            printf '  FAIL  %s\n          expected to contain: %s\n          actual:              %s\n' \
                "$name" "$needle" "$hay"
            FAIL=$((FAIL + 1)) ;;
    esac
}

echo
echo "Testing: $SCRIPT"
echo

# ------------------------------------------------- 1. the docker-proxy blind spot
echo "1. Liveness probe (the bug that shipped)"
reset_state
start_healthy
run_watchdog
check "healthy runner: no restart" "0" "$(restart_count)"
check "healthy runner: no failure counted" "0" "$(fail_count)"

reset_state
start_proxyonly
run_watchdog
proxy_seen=$(fail_count)
check "crash-looping runner behind docker-proxy: counted as a failure" "1" "$proxy_seen"
if [ "$proxy_seen" = "0" ]; then
    echo "        ^ THIS is the production bug: the TCP connect was accepted, so a"
    echo "          crash-looping runner looked healthy. 2026-09-02, 4.9 hours."
fi

reset_state
start_dead
run_watchdog
check "nothing listening: counted as a failure" "1" "$(fail_count)"

# ------------------------------------------------------ 2. escalation behaviour
echo
echo "2. Escalation (3 strikes, then restart)"
reset_state
start_dead
run_watchdog
check_contains "failure 1 of 3 logs WARN" "WARN" "$(log_tail)"
check "failure 1 does not restart" "0" "$(restart_count)"
run_watchdog
check_contains "failure 2 of 3 logs WARN" "WARN" "$(log_tail)"
check "failure 2 does not restart" "0" "$(restart_count)"
run_watchdog
check_contains "failure 3 of 3 acts" "RESTART" "$(log_tail)"
check "failure 3 restarts exactly once" "1" "$(restart_count)"
check "counter resets after acting" "0" "$(fail_count)"

# ------------------------------------------------------------- 3. the hourly cap
echo
echo "3. Blast radius cap (2 restarts per hour)"
run_watchdog; run_watchdog; run_watchdog
check "second restart allowed" "2" "$(restart_count)"
run_watchdog; run_watchdog; run_watchdog
check_contains "third attempt is BLOCKED" "BLOCKED" "$(log_tail)"
check "third attempt does NOT restart" "2" "$(restart_count)"

# ------------------------------------------------------------- 4. recovery reset
echo
echo "4. Recovery"
reset_state
start_dead
run_watchdog
run_watchdog
start_healthy
run_watchdog
check_contains "recovery logs OK" "OK" "$(log_tail)"
check "recovery resets the counter" "0" "$(fail_count)"

# -------------------------------------------------- 5. a deliberate `app stop`
echo
echo "5. Stopped container is not a fault"
reset_state
start_dead
echo "false" > "$SANDBOX/container_running"
run_watchdog
run_watchdog
run_watchdog
run_watchdog
check "stopped container never restarted" "0" "$(restart_count)"
check "stopped container writes no log" "0" "$(log_lines)"

# ------------------------------------------------------------------ 6. log trim
echo
echo "6. Log stays bounded"
reset_state
start_dead
seq 1 900 > "$STATE/watchdog.log"
run_watchdog
lines=$(log_lines)
if [ "$lines" -le 500 ]; then
    check "log trimmed to <= 500 lines" "yes" "yes"
else
    check "log trimmed to <= 500 lines" "<=500" "$lines"
fi

# --------------------------------------------------- 7. a missing tool is not a fault
# This is how watchdog v2 died: an SDK update deleted `netstat` from the runner
# image, `docker exec` exited 126, and `||` treated that as an unhealthy target.
# 720 restarts a day for two months. A tool that is missing must stop the
# watchdog, never trigger it.
echo
echo "7. A broken probe tool must not look like a failure"

# (a) the tool exists but is broken -- exits non-zero for its own reasons
reset_state
start_dead
printf '#!/usr/bin/env bash\nexit 127\n' > "$BIN/curl"
chmod +x "$BIN/curl"
run_watchdog; run_watchdog; run_watchdog; run_watchdog
check "broken probe tool never restarts the container" "0" "$(restart_count)"
check "broken probe tool counts no failures" "0" "$(fail_count)"
check_contains "broken probe tool is reported" "ERROR" "$(log_tail)"
rm -f "$BIN/curl"

# (b) the tool is gone entirely -- this is literally what killed v2
reset_state
start_dead
printf '#!/usr/bin/env bash\necho "not found" >&2\nexit 127\n' > "$BIN/curl"
chmod +x "$BIN/curl"
run_watchdog; run_watchdog; run_watchdog; run_watchdog
check "missing probe tool never restarts the container" "0" "$(restart_count)"
rm -f "$BIN/curl"

# (c) a genuine transport failure MUST still be actioned -- the guard above must
#     not have made the watchdog blind all over again
reset_state
start_dead
run_watchdog; run_watchdog; run_watchdog
check "real outage still restarts" "1" "$(restart_count)"

echo
echo "-----------------------------------------"
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
echo
if [ "$FAIL" -gt 0 ]; then exit 1; fi
exit 0
