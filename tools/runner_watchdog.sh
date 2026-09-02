#!/usr/bin/env bash
#
# Video runner watchdog for the Arduino UNO Q object-detection app.
#
# Restarts the Edge Impulse video runner container when it stops serving its
# real service port, and REFUSES to do so more than a few times per hour.
#
# Three previous watchdogs on this board failed, each in a different way (see
# CLAUDE.md, "Host watchdogs"). v1 and v2 were unbounded and restarted a healthy
# container for weeks. v3 was bounded and safe but BLIND: its probe could not
# fail, so it never acted at all. The rules below exist because of all three --
# read CLAUDE.md before changing any of this.
#
#   - Probe port 4912 FROM THE HOST, with an HTTP request. It must prove the
#     service ANSWERS, not merely that something accepted a socket: `docker-proxy`
#     accepts on the published port for the container's whole lifetime, so a bare
#     TCP connect is a permanent false positive (that was watchdog v3, blind
#     through a 4.9-hour outage on 2026-09-02).
#   - Never `docker exec` (the runner image's toolset changes without warning --
#     `netstat` vanished in an SDK update and broke watchdog v2). Never Docker
#     health status (it is inverted: `healthy` while crash-looping, `unhealthy`
#     while streaming fine).
#   - Require several CONSECUTIVE failures before acting.
#   - Cap restarts per hour. Assume this probe will eventually be wrong; the cap
#     is what turns "wrong" into a nuisance instead of a two-month outage.
#   - No `||` chaining. `||` fires on exit 126/127 too -- that is exactly how the
#     previous watchdog broke.
#   - Never touch a container that is stopped: that is a deliberate `app stop`.
#
# Install (runs every 2 minutes):
#   crontab -e
#   */2 * * * * /home/arduino/ArduinoApps/detect-objects-on-camera-modified/tools/runner_watchdog.sh
#
# Test:
#   ./tools/test_runner_watchdog.sh
#
# Run the suite after ANY change here. Do not "verify" this script by pointing it
# at a closed port -- that is what was done in August 2026, and it passed while
# the watchdog was blind, because a closed port has no docker-proxy in front of
# it. The suite reproduces the real failure shape instead.

set -u

CONTAINER="${WATCHDOG_CONTAINER:-detect-objects-on-camera-modified-ei-video-obj-detection-runner-1}"
PROBE_HOST="${WATCHDOG_HOST:-127.0.0.1}"
PROBE_PORT="${WATCHDOG_PORT:-4912}"
PROBE_TIMEOUT="${WATCHDOG_PROBE_TIMEOUT:-5}"
PROBE_RC=0
FAIL_THRESHOLD="${WATCHDOG_FAIL_THRESHOLD:-3}"
MAX_RESTARTS_PER_HOUR="${WATCHDOG_MAX_RESTARTS:-2}"
DRY_RUN="${WATCHDOG_DRY_RUN:-0}"

STATE_DIR="${WATCHDOG_STATE_DIR:-$HOME/.local/state/runner-watchdog}"
FAIL_FILE="$STATE_DIR/consecutive_failures"
HIST_FILE="$STATE_DIR/restart_history"
LOG_FILE="$STATE_DIR/watchdog.log"
LOG_MAX_LINES=500

mkdir -p "$STATE_DIR"

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*" >> "$LOG_FILE"
}

# Keep our own log bounded. The in-app recovery loop wrote 36,087 lines in 55
# minutes and rotated away the evidence of the failure it was reacting to.
trim_log() {
    local lines
    if [ ! -f "$LOG_FILE" ]; then return; fi
    lines=$(wc -l < "$LOG_FILE" 2>/dev/null)
    if [ -z "$lines" ]; then return; fi
    if [ "$lines" -le "$LOG_MAX_LINES" ]; then return; fi
    tail -n "$LOG_MAX_LINES" "$LOG_FILE" > "$LOG_FILE.tmp"
    mv "$LOG_FILE.tmp" "$LOG_FILE"
}

read_count() {
    local n
    n=$(cat "$FAIL_FILE" 2>/dev/null)
    case "$n" in
        ''|*[!0-9]*) echo 0 ;;
        *) echo "$n" ;;
    esac
}

# Exit 0 only if the container exists AND is running.
container_running() {
    local state
    state=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)
    if [ "$state" = "true" ]; then return 0; fi
    return 1
}

# THE decision signal.
#
#   returns 0  the service answered           -> healthy
#   returns 1  the service did not answer     -> count a failure
#   returns 2  the probe itself is unreliable -> DO NOTHING, log it
#
# A bare TCP connect is not enough. `docker-proxy` holds the published host port
# for as long as the container exists, whatever is happening inside it, so a
# connect succeeds even while the runner is crash-looping. That is not a theory:
# it is why this watchdog logged nothing through a 4.9-hour outage on
# 2026-09-02. Reproduced by tools/test_runner_watchdog.sh, case "crash-looping
# runner behind docker-proxy".
#
# Note there is no `-f`: any HTTP response at all -- including a 404 -- proves
# something is alive and serving. Only the transport-level failures below mean
# the runner is down. Every OTHER exit code means curl could not do its job, and
# the one thing this watchdog must never do is read its own broken tooling as a
# sick container. That is precisely how v2 turned into 720 restarts a day.
probe_service() {
    curl -sS --max-time "$PROBE_TIMEOUT" -o /dev/null \
        "http://${PROBE_HOST}:${PROBE_PORT}/" >/dev/null 2>&1
    PROBE_RC=$?
    case "$PROBE_RC" in
        0)              return 0 ;;   # answered
        7)              return 1 ;;   # connection refused
        28)             return 1 ;;   # timed out
        35|52|56)       return 1 ;;   # handshake / empty reply / reset by peer
        *)              return 2 ;;   # curl missing, misused, or broken
    esac
}

# A missing tool must never look like a failing target. Watchdog v2 died of
# exactly that: an SDK update deleted `netstat`, exec exited 126, and `||` read
# it as "unhealthy" -- 720 restarts a day for two months.
require_tools() {
    local t
    for t in docker curl awk; do
        if ! command -v "$t" >/dev/null 2>&1; then
            log "ERROR   required tool '${t}' not found; refusing to act"
            trim_log
            exit 0
        fi
    done
}

# Number of restarts recorded in the last hour, pruning older entries.
recent_restarts() {
    local now cutoff kept=0
    now=$(date +%s)
    cutoff=$((now - 3600))
    if [ -f "$HIST_FILE" ]; then
        awk -v c="$cutoff" '$1 ~ /^[0-9]+$/ && $1 >= c' "$HIST_FILE" > "$HIST_FILE.tmp"
        mv "$HIST_FILE.tmp" "$HIST_FILE"
        kept=$(wc -l < "$HIST_FILE")
    fi
    echo "$kept"
}

main() {
    require_tools

    if ! container_running; then
        # Deliberately stopped, or the app was never started. Not our business.
        echo 0 > "$FAIL_FILE"
        exit 0
    fi

    probe_service
    local verdict=$?

    if [ "$verdict" -eq 2 ]; then
        # Our own tooling failed. Say so and stop: an unreliable probe must
        # never be allowed to look like an unhealthy container.
        log "ERROR   probe unusable (curl exit ${PROBE_RC}); refusing to act"
        trim_log
        exit 0
    fi

    if [ "$verdict" -eq 0 ]; then
        local previous
        previous=$(read_count)
        if [ "$previous" -gt 0 ]; then
            log "OK      service on ${PROBE_PORT} answering again after ${previous} failure(s)"
        fi
        echo 0 > "$FAIL_FILE"
        trim_log
        exit 0
    fi

    local failures
    failures=$(($(read_count) + 1))
    echo "$failures" > "$FAIL_FILE"

    if [ "$failures" -lt "$FAIL_THRESHOLD" ]; then
        log "WARN    service on ${PROBE_PORT} not answering (${failures}/${FAIL_THRESHOLD})"
        trim_log
        exit 0
    fi

    local restarts
    restarts=$(recent_restarts)
    if [ "$restarts" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
        log "BLOCKED service on ${PROBE_PORT} not answering (${failures}) but ${restarts} restart(s) in the last hour; cap is ${MAX_RESTARTS_PER_HOUR}. Not restarting -- investigate by hand."
        trim_log
        exit 0
    fi

    if [ "$DRY_RUN" = "1" ]; then
        log "DRYRUN  would restart ${CONTAINER} (failures=${failures}, restarts_this_hour=${restarts})"
        echo 0 > "$FAIL_FILE"
        trim_log
        exit 0
    fi

    log "RESTART service on ${PROBE_PORT} not answering ${failures}x; restarting ${CONTAINER} (${restarts} prior restart(s) this hour)"
    date +%s >> "$HIST_FILE"

    docker restart "$CONTAINER" > /dev/null 2>&1
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        log "RESTART done"
    else
        log "RESTART FAILED with exit code ${rc}"
    fi

    echo 0 > "$FAIL_FILE"
    trim_log
}

main "$@"
