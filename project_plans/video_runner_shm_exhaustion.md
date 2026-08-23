# Video Runner Crash Loop — /dev/shm Exhaustion After an OOM Kill

**Date:** 2026-08-23
**Symptom:** No live image in the web UI. Video runner restarting every ~10s for
over two hours (~840 times). Docker reported the container as `healthy` throughout.
**Root cause:** A global OOM kill orphaned ~1,165 JPEG frames in the runner's 64 MB
`/dev/shm` tmpfs. With the tmpfs 100% full, every restart failed on its first frame
write, leaking one more temp dir per attempt. Self-sustaining; no recovery possible
without external intervention.
**Status:** Resolved by clearing the tmpfs. Underlying runner bugs are upstream.

---

## 1. Timeline

| Time (UTC) | Event |
|---|---|
| 2026-08-13 13:33 | Board boots. Runner starts, creates `/dev/shm/edge-impulse-clir4tIXH` |
| 2026-08-13 – 08-23 | Normal operation, 10 fps. `node` RSS grows ~200 MB → 938 MB |
| **08-23 18:45:34** | **Global OOM kill** — kernel kills `node` (largest RSS on a 1.7 GB board) |
| 08-23 18:45–18:47 | Orphaned GStreamer keeps writing frames until the tmpfs is 100% full |
| 08-23 18:47:22 → 21:16 | Crash loop: ~840 restarts, each leaking one empty temp dir |
| 08-23 21:16:30 | `rm -rf /dev/shm/edge-impulse-cli*` — recovered on the next cycle |

## 2. Evidence

**The OOM (single event since boot):**

```
oom-kill:constraint=CONSTRAINT_NONE,...,global_oom,
  task_memcg=/system.slice/docker-67fdfe52...scope,task=node,pid=1480,uid=1000
Out of memory: Killed process 1480 (node) total-vm:23432996kB, anon-rss:960480kB
```

`constraint=CONSTRAINT_NONE` + `global_oom` = board-wide, not a container limit.
Neither container sets one (`HostConfig.Memory=0`).

Top RSS at kill time (4 KB pages) — `node` dwarfs everything:

| Process | Pages | ≈ |
|---|---|---|
| node | 240,088 | **938 MB** |
| yolo-x-nano.eim | 10,700 | 42 MB |
| python (this app) | 3,499 | 14 MB |

**The full tmpfs:**

```
shm  64M  64M  0  100% /dev/shm
65504 KB  /dev/shm/edge-impulse-cliNm6m9G   (1,165 files)
          resized7834510.jpg … resized7835674.jpg
```

Frame numbers ~7.8 M match 10 days at 10 fps. Directory mtime `18:47:21` — ~2 minutes
after the OOM, i.e. the orphaned pipeline wrote until the disk filled, then stopped.

**The loop, timed precisely:**

```
21:04:08.849  [GST] In tcp server mode, waiting on 0.0.0.0:5050
21:04:10.530  [RUN] Connected to camera "streaming-tcp-server"
21:04:10.600  [RUN] camera error Capture process failed with code 1   <- 70 ms later
21:04:10.662  Application exited with error (Exit Code: 1). Restarting in 1 seconds
```

70 ms is the tell: GStreamer was **not** waiting for frames and timing out. It died on
its first write. `find / -name 'resized*.jpg'` returned 0 — it never emitted an image.

**Leak accounting at time of fix:** 836 `edge-impulse-cli*` dirs (one per restart, all
empty but one) and 887 `shm-*` files. The `shm-*` files are sparse — 692,224 bytes
apparent, `du` reports 0 blocks — so they consume no space and were left in place.

## 3. Why it could never self-heal

The tmpfs is only recreated when the **container** is recreated. The runner's own
supervisor (`start-runner.sh`, PID 1446, alive the whole time) restarts `node`, which
reuses the same full filesystem. 840 restarts made no progress and none ever could.

`docker restart` would have fixed it — the shm mount is torn down and remounted.

## 4. Why nothing noticed

- **Docker health status is inverted.** It read `healthy` for the entire outage,
  because port 5050 LISTENs only while *waiting* for a feed. It flipped to `unhealthy`
  the moment the stream was restored (failing streak 33 and climbing). This is a
  readiness gate being misread as a liveness probe — see `CLAUDE.md`.
- **In-app recovery cannot work.** `restart_video_runner_container()` failed on all
  five methods every cycle (no Docker socket, no API, no CLI), as it has since
  `d380c87` in January.
- **The failed recovery destroyed the evidence.** Its unbounded retry loop wrote
  **36,087 log lines in 55 minutes**, rotating the main container's log
  (`max-size: 5m, max-file: 2`) past the 18:45 failure window. The original cause had
  to be recovered from the kernel journal instead.

## 5. Ruled out

| Hypothesis | Disproved by |
|---|---|
| Camera unplugged / USB fault | Zero kernel USB events since 2026-08-13 14:02 |
| App leaked the camera fd | The brick legitimately owns `/dev/video0`; by design |
| Brick camera loop dead | Alive, reconnecting 716×/hour, logging no capture errors |
| Disk full | Root fs 77% used, 2.2 GB free |
| CPU starvation | Load 2.0 on a quad-core |
| GStreamer failing to spawn | Observed running, RSS 5 → 8.5 MB per attempt |

## 6. Fix applied

```bash
docker exec ...-runner-1 sh -c 'rm -rf /dev/shm/edge-impulse-cli*'
```

Recovery on the next 10-second cycle, with no restart of anything:

| | Before | After |
|---|---|---|
| `/dev/shm` | 64 MB, 100% | 676 KB, 2% |
| Runner restarts | ~350/hour | 0 in 90 s |
| `node` uptime / RSS | dies every 10 s | stable, 160 MB |
| App stream | `disconnected` | `fps=10.0 disconnects=0 frame_age=0.0s` |

## 7. Upstream bugs (not fixable in this repo)

Both belong to the Edge Impulse runner image (`ei-models-runner:0.11.2`):

1. **Memory growth** — ~200 MB → 938 MB over 10 days. On a 1.7 GB board this
   guarantees a periodic OOM.
2. **No cleanup of `/dev/shm` on abnormal exit** — turns a recoverable crash into a
   permanent one.

Neither the brick compose nor `.cache/app-compose-overrides.yaml` is ours to edit;
both are regenerated by App Lab. `shm_size` and `mem_limit` would mitigate but would
not survive an SDK update.

## 8. Recommended follow-ups

1. **A correctly-bounded host watchdog.** Written as `tools/runner_watchdog.sh`:
   probes 4912 from the host, requires 3 consecutive failures, caps restarts at 2/hour,
   and ignores a deliberately stopped container. Not installed by default — see
   `CLAUDE.md`. Two previous watchdogs failed by being unbounded, so the cap matters
   more than the probe: assume the probe will eventually be wrong.
2. **Remove or bound `restart_video_runner_container()`.** It cannot work, and its
   log flood actively obstructs diagnosis of the failures it reacts to.
3. **Report both upstream bugs to Arduino / Edge Impulse.**

## 9. Expect a recurrence

Nothing in this repo prevents the OOM. With ~75 MB/day of growth on a 1.7 GB board,
another kill is likely after roughly ten days of continuous runtime unless something
restarts the runner in between.
