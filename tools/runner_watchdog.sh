#!/usr/bin/env bash
#
# Video runner watchdog for the Arduino UNO Q object-detection app.
#
# Restarts the Edge Impulse video runner container when it stops serving its
# real service port, and REFUSES to do so more than a few times per hour.
#
# Two previous watchdogs on this board caused far more damage than they
# prevented (see CLAUDE.md, "Host watchdogs"). Both were unbounded: one failed
# probe meant an immediate restart, every 2 minutes, forever. The rules below
# exist because of them -- read CLAUDE.md before changing any of this.
#
#   - Probe port 4912 FROM THE HOST. Never `docker exec` (the runner image's
#     toolset changes without warning -- `netstat` vanished in an SDK update and
#     broke the last watchdog). Never Docker health status (it is inverted:
#     `healthy` while crash-looping, `unhealthy` while streaming fine).
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
# Test without restarting anything:
#   WATCHDOG_DRY_RUN=1 WATCHDOG_PORT=59999 ./tools/runner_watchdog.sh

set -u

CONTAINER="${WATCHDOG_CONTAINER:-detect-objects-on-camera-modified-ei-video-obj-detection-runner-1}"
PROBE_HOST="${WATCHDOG_HOST:-127.0.0.1}"
PROBE_PORT="${WATCHDOG_PORT:-4912}"
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

# Exit 0 if the service port accepts a TCP connection.
probe_port() {
    timeout 2 bash -c "exec 3<>/dev/tcp/${PROBE_HOST}/${PROBE_PORT}" 2>/dev/null
    return $?
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
    if ! container_running; then
        # Deliberately stopped, or the app was never started. Not our business.
        echo 0 > "$FAIL_FILE"
        exit 0
    fi

    if probe_port; then
        local previous
        previous=$(read_count)
        if [ "$previous" -gt 0 ]; then
            log "OK      port ${PROBE_PORT} responding again after ${previous} failure(s)"
        fi
        echo 0 > "$FAIL_FILE"
        trim_log
        exit 0
    fi

    local failures
    failures=$(($(read_count) + 1))
    echo "$failures" > "$FAIL_FILE"

    if [ "$failures" -lt "$FAIL_THRESHOLD" ]; then
        log "WARN    port ${PROBE_PORT} unreachable (${failures}/${FAIL_THRESHOLD})"
        trim_log
        exit 0
    fi

    local restarts
    restarts=$(recent_restarts)
    if [ "$restarts" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
        log "BLOCKED port ${PROBE_PORT} unreachable (${failures}) but ${restarts} restart(s) in the last hour; cap is ${MAX_RESTARTS_PER_HOUR}. Not restarting -- investigate by hand."
        trim_log
        exit 0
    fi

    if [ "$DRY_RUN" = "1" ]; then
        log "DRYRUN  would restart ${CONTAINER} (failures=${failures}, restarts_this_hour=${restarts})"
        echo 0 > "$FAIL_FILE"
        trim_log
        exit 0
    fi

    log "RESTART port ${PROBE_PORT} unreachable ${failures}x; restarting ${CONTAINER} (${restarts} prior restart(s) this hour)"
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
