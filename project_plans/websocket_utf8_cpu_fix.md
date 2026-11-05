# Websocket UTF-8 validation CPU fix

Status: applied 2026-11-05 (JP approved).

## Problem

`capture.py` receives the runner's live frames over Socket.IO (`python-socketio` →
`python-engineio` → `websocket-client`). Each `image` event is a ~75–88 KB base64 text
frame at ~9.5 fps.

`websocket-client` validates every text frame as UTF-8. With the compiled `wsaccel`
helper it is cheap; without it, it falls back to a pure-Python per-byte DFA. `wsaccel`
is absent from `python-apps-base` 0.11.0 and 0.13.2.

Measured on the board, 2026-11-01:

| Measurement | Result |
|---|---|
| `validate_utf8` on an 80 KB frame | 85–93 ms |
| Fresh test client, default settings | 9.5 fps, **78–84 % of one core** |
| Same client, `skip_utf8_validation=True` | 9.5 fps, **3 % of one core** |
| `base64.b64decode` + `cv2.imdecode` per frame | ~7 ms |

Effects: the main app tops out around 10–11 fps (lower with bigger daytime frames),
the receive thread holds the GIL so Socket.IO pongs are late, and with a browser viewer
also connected the app falls behind and reconnects (528 disconnects with a viewer vs 16
without, 2026-11-02 to 11-05). The runner `node` leak grew ~200 MB/day with a viewer vs
~85 MB/day without; reconnects are a suspected contributor.

## Change

One argument in `_setup_socketio()`:

```python
socketio.Client(..., websocket_extra_options={"skip_utf8_validation": True})
```

`engineio` passes `websocket_extra_options` to `websocket.create_connection()`.

Safe because the peer is the runner container on the same board and the payload is
base64 ASCII; a malformed frame would at worst fail `b64decode`/`imdecode`, which
`_process_frame_data` already catches.

## Verify

- `docker stats`: main container CPU drops from ~100–115 %
- `[STREAM]` fps stays ~10, disconnects stay near 0 with a browser viewer open
- `memlog.log`: runner `node` growth rate with a viewer, compared with ~200 MB/day

## Rollback

Remove the `websocket_extra_options` argument and restart the main container.
