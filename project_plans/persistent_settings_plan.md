# Plan: Persist Detection Confidence & Label Across Restarts

## Problem

After a power failure or restart, `DETECTION_CONFIDENCE` and `DETECTION_LABEL` reset to hardcoded defaults (0.6 and "bottle"). User overrides from the web UI are lost.

## Option Evaluation

### Option A: Linux-side file in `data/` directory (Recommended)

The `data/` directory is already bind-mounted from the host filesystem into the Docker container (confirmed by `app-compose.yaml`). The existing `imageslist.log` file already survives restarts via this mechanism. Adding a `data/settings.json` follows the exact same proven pattern with zero new dependencies.

**Pros:**
- Zero new dependencies or architectural changes
- Follows the exact pattern already used for `imageslist.log` in `persistence.py`
- Pure Python, no sketch changes needed
- Survives container restarts and power cycles (bind mount to host eMMC)
- Easy to debug (just read the JSON file)

**Cons:** None meaningful for this use case.

### Option B: Arduino MCU NVS via Bridge (Not recommended)

The sketch uses `Arduino_RouterBridge` but only for fire-and-forget calls (LED control). Return values from Bridge calls are not used anywhere and support is undocumented.

- `Preferences.h` is an ESP32 API — uncertain if the UNO Q MCU supports it
- `EEPROM.h` support is board-dependent
- Would require new sketch functions, reflashing, and solving the Bridge return-value problem
- Over-engineered for 2 simple values when the Linux filesystem already works

**Verdict: Option A.** Simple, proven, no sketch changes needed.

---

## Implementation Plan (Option A)

### Files to modify

| File | Change |
|------|--------|
| `python/persistence.py` | Add `SETTINGS_FILE`, `load_settings()`, `save_settings()` |
| `python/inner_main.py` | Import new functions, load settings on startup, save on override |

No changes needed to `ui_handlers.py`, the sketch, the frontend, or any config files.

### Step 1: Add settings functions to `persistence.py`

Add `SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")` and two functions:

- **`load_settings(defaults: dict) -> dict`** — Reads `data/settings.json` if it exists, merges with provided defaults as fallback. If file is missing or corrupt, returns defaults with a log warning.

- **`save_settings(settings: dict) -> None`** — Writes dict as JSON to `data/settings.json`. Uses write-to-temp-then-rename pattern for atomic writes (prevents corruption if power is lost mid-write).

Settings file format:
```json
{
  "detection_confidence": 0.6,
  "detection_label": "bottle"
}
```

### Step 2: Load settings on startup in `inner_main.py`

After `init_data_directories()` (line 97), call `load_settings()` and use the returned values for `DETECTION_CONFIDENCE` and `DETECTION_LABEL` instead of hardcoded defaults. The hardcoded values become the fallback defaults passed to `load_settings()`.

### Step 3: Save settings on override in `inner_main.py`

Replace the `globals().__setitem__` lambdas in the `override_th` and `override_label` WebSocket handlers with wrapper functions that also call `save_settings()` after updating the global variable.

### Step 4: Debounced writes to minimize disk I/O

Settings changes (especially the confidence slider) can fire rapidly. To avoid writing to disk on every change:

- `save_settings()` stores the latest settings in a `_pending_settings` variable and starts/resets a `threading.Timer` (default 3 seconds via `SETTINGS_SAVE_DEBOUNCE`).
- When the timer fires, `_debounce_fire()` writes the pending settings to disk atomically.
- `flush_settings()` immediately writes any pending settings and cancels the timer. Called from `shutdown_handler` in `inner_main.py` to ensure the last change is never lost on app stop.

### Edge Cases

- **First run (no settings.json):** `load_settings()` returns defaults. File is only created on first user override.
- **Rapid slider changes:** Debounced — only the last value within the 3-second window is written. Atomic write prevents corruption.
- **App shutdown with pending save:** `flush_settings()` in the shutdown handler writes immediately before exit.
- **File permissions:** Container already writes to `data/` (proven by `imageslist.log`). No issues expected.

## Verification

1. Start the app, change confidence and/or label via the web UI
2. Restart the app (`arduino-app-cli app stop/start`)
3. Confirm the UI shows the previously set values, not the defaults
4. Delete `data/settings.json` and restart — should fall back to defaults gracefully
5. Rapidly drag the confidence slider — confirm only one `[SETTINGS] Saved settings` log line appears after changes stop
