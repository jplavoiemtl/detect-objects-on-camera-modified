#!/usr/bin/env bash
#
# Read-only memory logger for the video runner's `node` process.
#
# The runner leaks memory (upstream bug, see CLAUDE.md and
# project_plans/video_runner_shm_exhaustion.md). In August the leak was
# ~75 MB/day; by late October it was ~185 MB/day and watchdog restarts came
# every 12, then 7, then 5 days. This script records one line per run so the
# leak rate can be correlated with stream viewers and reconnects:
#
#   node RSS / swap, host MemAvailable / swap used,
#   connections on 4912 (internal = main container, external = browsers),
#   main-app Socket.IO disconnects and average fps over the last interval.
#
# It never restarts, stops, or changes anything. Every probe failure is
# recorded as "?" rather than acted on.
#
# Install (runs every 10 minutes):
#   */10 * * * * /home/arduino/ArduinoApps/detect-objects-on-camera-modified/tools/runner_memlog.sh

set -u

RUNNER="${MEMLOG_RUNNER:-detect-objects-on-camera-modified-ei-video-obj-detection-runner-1}"
MAIN="${MEMLOG_MAIN:-detect-objects-on-camera-modified-main-1}"
INTERVAL="${MEMLOG_INTERVAL:-10m}"
STATE_DIR="${MEMLOG_STATE_DIR:-$HOME/.local/state/runner-watchdog}"
LOG_FILE="$STATE_DIR/memlog.log"
LOG_MAX_LINES=4500   # ~31 days at one line per 10 minutes

mkdir -p "$STATE_DIR"

status_kb() {
    # $1 = pid, $2 = field (VmRSS, VmSwap)
    awk -v f="$2:" '$1 == f { print int($2 / 1024) }' "/proc/$1/status" 2>/dev/null
}

node_pid=$(docker top "$RUNNER" -eo pid,comm 2>/dev/null | awk '$2 == "node" { print $1; exit }')

node_rss="?"; node_swap="?"; conn_int="?"; conn_ext="?"
if [ -n "$node_pid" ]; then
    node_rss=$(status_kb "$node_pid" VmRSS)
    node_swap=$(status_kb "$node_pid" VmSwap)
    # Established sockets on local port 4912 (0x1330). The remote address is
    # little-endian hex, so a peer in 172.x.x.x ends in "AC".
    read -r conn_int conn_ext < <(awk 'NR > 1 && $4 == "01" && $2 ~ /:1330$/ {
            split($3, r, ":"); if (substr(r[1], 7, 2) == "AC") i++; else e++ }
        END { print i + 0, e + 0 }' "/proc/$node_pid/net/tcp" 2>/dev/null)
fi

mem_avail=$(awk '$1 == "MemAvailable:" { print int($2 / 1024) }' /proc/meminfo)
swap_used=$(awk '$1 == "SwapTotal:" { t = $2 } $1 == "SwapFree:" { f = $2 }
    END { print int((t - f) / 1024) }' /proc/meminfo)

# Slow host-level decline (2026-11-01): after 80 days of uptime Wi-Fi throughput
# had fallen to 0.4-0.58 MB/s (2.8-3.2 MB/s after a reboot) while unreclaimable
# kernel slab had grown 42 -> 114 MB. Counters below are cumulative since boot;
# compare consecutive lines to get rates.
sunreclaim=$(awk '$1 == "SUnreclaim:" { print int($2 / 1024) }' /proc/meminfo)
read -r pswpin pswpout < <(awk '$1 == "pswpin" { i = $2 } $1 == "pswpout" { o = $2 }
    END { print i + 0, o + 0 }' /proc/vmstat)
read -r tcp_out tcp_retrans < <(awk '$1 == "Tcp:" && $2 ~ /^[0-9]/ { print $12, $13 }' /proc/net/snmp)
wlan_tx_mb=$(awk '{ print int($1 / 1048576) }' /sys/class/net/wlan0/statistics/tx_bytes 2>/dev/null)

main_log=$(docker logs --since "$INTERVAL" "$MAIN" 2>&1)
disconnects=$(printf '%s\n' "$main_log" | grep -c "Socket.IO disconnected")
fps=$(printf '%s\n' "$main_log" | awk '/\[STREAM\] fps=/ {
        for (k = 1; k <= NF; k++) if ($k ~ /^fps=/) { s += substr($k, 5); n++ } }
    END { if (n) printf "%.1f", s / n; else print "?" }')

printf '%s node_pid=%s node_rss_mb=%s node_swap_mb=%s mem_avail_mb=%s swap_used_mb=%s conn4912_int=%s conn4912_ext=%s disconnects=%s fps=%s sunreclaim_mb=%s pswpin=%s pswpout=%s tcp_out=%s tcp_retrans=%s wlan_tx_mb=%s\n' \
    "$(date '+%Y-%m-%d %H:%M:%S%z')" "${node_pid:-?}" "${node_rss:-?}" "${node_swap:-?}" \
    "$mem_avail" "$swap_used" "${conn_int:-?}" "${conn_ext:-?}" "$disconnects" "$fps" \
    "${sunreclaim:-?}" "${pswpin:-?}" "${pswpout:-?}" "${tcp_out:-?}" "${tcp_retrans:-?}" "${wlan_tx_mb:-?}" >> "$LOG_FILE"

lines=$(wc -l < "$LOG_FILE")
if [ "$lines" -gt "$LOG_MAX_LINES" ]; then
    tail -n "$LOG_MAX_LINES" "$LOG_FILE" > "$LOG_FILE.tmp"
    mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
