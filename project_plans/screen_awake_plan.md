# Screen Awake Feature Plan

## Goal

Add a **mobile-only** toggle button to keep the iPhone screen awake while monitoring the live camera feed.
The button defaults to **disabled** (screen sleeps normally) and activates on tap.
Desktop users (≥ 851 px) must not see this button.

---

## Background & Context

When monitoring the live video feed on an iPhone, the screen automatically dims and locks after the system timeout (~30–60 s). The **Wake Lock API** (`navigator.wakeLock.request('screen')`) is the modern standard for preventing this.

### HTTPS requirement

The Wake Lock API requires a **secure context** (HTTPS or `localhost`).
The app is served over **`http://arduino-q.local:7000`** — this is **not** a secure context on Safari iOS, so the native API call is blocked.

### Critical discovery: `navigator.wakeLock` exists on iOS over HTTP but silently fails

On iOS Safari, `'wakeLock' in navigator` returns `true` even over HTTP. However, calling `navigator.wakeLock.request('screen')` throws a `NotAllowedError`. This is critical because any code (including NoSleep.js itself) that checks for the API's existence will try the native path, fail, and may not properly fall back to the video trick.

---

## Implementation — What actually works

### Chosen approach: NoSleep.js v0.12.0 library with Wake Lock API masking

After testing multiple approaches (hand-rolled base64 video, Web Audio API oscillator, custom video+audio combinations), the solution that reliably keeps the iPhone screen awake over HTTP is the **NoSleep.js library v0.12.0** with a critical pre-load fix.

### The fix: mask `navigator.wakeLock` on HTTP

Before loading NoSleep.js, mask the Wake Lock API so the library skips its broken native path and goes straight to its battle-tested video fallback:

```javascript
// In index.html, BEFORE loading nosleep.min.js:
if (location.protocol === 'http:' && 'wakeLock' in navigator) {
    Object.defineProperty(navigator, 'wakeLock', { value: undefined, configurable: true });
}
```

Without this fix, NoSleep.js detects the Wake Lock API, tries it, fails silently over HTTP, and never falls back to the video trick. With the fix, NoSleep.js uses its internal video fallback which works reliably.

### app.js implementation

```javascript
const _noSleep = new NoSleep();
let wakeLockEnabled = false;

function toggleWakeLock() {
    if (wakeLockEnabled) {
        _noSleep.disable();
        wakeLockEnabled = false;
        updateWakeLockButton();
        showToast('Screen Awake off');
    } else {
        _noSleep.enable().then(() => {
            wakeLockEnabled = true;
            updateWakeLockButton();
            showToast('Screen Awake on');
        }).catch(() => {
            showToast('Screen Awake failed — tap again');
        });
    }
}
```

### Known side effect

The hidden video element takes over the iOS media session, which can interrupt Spotify/CarPlay audio playback. This is an accepted trade-off — there is no workaround over HTTP. Enabling HTTPS + native Wake Lock API would eliminate this conflict (see "Future improvement" below).

---

## Approaches that did NOT work over HTTP

These were all tested on iPhone (iOS 18+) and failed to prevent screen sleep:

1. **Hand-rolled base64 MP4 (muted)** — `video.play()` succeeded but iOS does not prevent sleep for muted videos.

2. **Hand-rolled base64 MP4 (unmuted)** — Used the real NoSleep.js MP4 blob unmuted. `play()` succeeded but screen still dimmed after a few minutes. The inline base64 approach may be treated differently than the library's internal implementation.

3. **Web Audio API silent oscillator** — Created an `AudioContext` with a zero-gain oscillator connected to the speaker. iOS did not treat this as active audio playback for screen sleep purposes.

4. **Video + Web Audio combined** — Both approaches together still did not prevent screen sleep.

5. **Periodic video re-play interval** — Re-playing the video every 15 seconds did not help.

The NoSleep.js library's internal video handling (including its own MP4 blobs for different platforms, proper attribute handling, and platform detection) works where hand-rolled implementations do not.

---

## Button Design

### Placement

The button is placed **on the video container** (bottom-left, next to `#btnFullscreen` on the right), **visible only on mobile** (≤ 850 px). On desktop it is hidden.

### HTML (inside `#videoFeedContainer`)

```html
<button id="btnWakeLock" title="Keep screen awake"
    style="position: absolute; bottom: 10px; left: 10px; z-index: 50;
           background: rgba(0,0,0,0.5); border: none; padding: 8px;
           border-radius: 4px; color: white; cursor: pointer; display: none;">
    <svg id="wakeLockIcon" width="24" height="24" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round">
        <!-- Closed eye (eye-off) icon — default inactive state -->
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
        <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
        <line x1="1" y1="1" x2="23" y2="23"/>
    </svg>
</button>
```

### CSS (in `style.css`)

```css
@media (max-width: 850px) {
    #btnWakeLock {
        display: flex !important;
        align-items: center;
        justify-content: center;
    }
}

#btnWakeLock.active {
    background: rgba(245, 158, 11, 0.4) !important;
    box-shadow: 0 0 8px rgba(245, 158, 11, 0.6);
}
```

### Active / Inactive states

| State | Icon | Background | Meaning |
|---|---|---|---|
| Disabled (default) | Closed eye (eye-off) | Dark semi-transparent | Screen will sleep normally |
| Enabled | Open eye | Amber glow | Screen is being kept awake |

A `showToast()` notification confirms activation/deactivation.

---

## Files Modified

| File | Change |
|---|---|
| `assets/index.html` | Wake Lock API masking script, `nosleep.min.js` script tag, `#btnWakeLock` button |
| `assets/libs/nosleep.min.js` | NoSleep.js v0.12.0 library (17KB, MIT license) |
| `assets/app.js` | `toggleWakeLock()`, `updateWakeLockButton()` using `new NoSleep()` |
| `assets/style.css` | Mobile-only display for `#btnWakeLock`, `.active` amber glow style |

---

## Future Improvement: HTTPS for native Wake Lock API

If the WebUI brick ever supports HTTPS (or a reverse proxy is added), the native Wake Lock API can be used instead. This would eliminate:
- The CarPlay/Spotify audio session conflict
- The hidden video battery overhead
- The need for the `navigator.wakeLock` masking hack

The masking script in `index.html` only runs on `http:` protocol, so switching to HTTPS would automatically enable the native path through NoSleep.js.

---

## Testing Plan

- [x] Button appears on mobile viewport (≤ 850 px)
- [x] Button is **not visible** on desktop (≥ 851 px)
- [x] Default state: inactive (closed eye icon, no wake lock)
- [x] Tapping activates: icon switches to open eye, amber glow, toast confirms "Screen Awake on"
- [x] Screen stays on after the normal iPhone auto-lock timeout
- [ ] Tapping again: deactivates, icon returns to closed eye, toast confirms "Screen Awake off"
- [ ] Page reload resets to disabled (no persistence required)
