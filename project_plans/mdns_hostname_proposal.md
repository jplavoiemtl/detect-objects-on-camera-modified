# mDNS Hostname Proposal

## User Request

Replace IP-based access (`http://192.168.30.223:7000/`) with a friendly hostname like `arduinoq.local`.

## Investigation Summary

### What I Found

1. **mDNS is already expected to work**: The existing documentation (CLAUDE.md, README) already references `<board-name>.local:7000` access. Example: `unoq.local:7000`.

2. **The app code already includes mDNS fallback**: In `capture.py:263`, there's a fallback URL `http://unoq.local:{VIDEO_STREAM_PORT}` used for video stream reconnection.

3. **This is NOT an application code change**: mDNS hostnames are provided by the device's operating system (Avahi daemon on Linux), not by the Python application. The app only binds to port 7000 - the hostname resolution happens at the network layer.

## Assessment

**Is this a good idea?** Yes, absolutely. Using a friendly hostname is more professional and user-friendly than memorizing IP addresses.

**Is this an app code change?** No. This is a system/network configuration task, not a Python code modification.

## Why mDNS Might Not Be Working

Possible reasons the user currently uses IP instead of hostname:

1. **Device hostname not set**: The Arduino UNO Q may not have mDNS configured with the expected name
2. **Client-side mDNS support**: Windows requires Bonjour/iTunes to resolve `.local` domains (macOS and Linux work natively)
3. **Network isolation**: Some routers block mDNS multicast traffic between devices

## Options (Simplest to Most Complex)

### Option 1: Check Existing mDNS (No Changes)
**Effort: Minimal**

The device may already have an mDNS hostname. Try:
- `unoq.local:7000` (suggested in current docs)
- `<device-hostname>.local:7000`

On the Arduino UNO Q, run:
```bash
hostname
```
Then access `http://<hostname>.local:7000`.

**Prerequisite**: Windows users need Bonjour installed (comes with iTunes or Apple's standalone Bonjour Print Services).

### Option 2: Configure Hostname on Device (System Config)
**Effort: Low**

SSH into the Arduino UNO Q and set the hostname:
```bash
sudo hostnamectl set-hostname arduinoq
```

The Avahi daemon (mDNS responder) should automatically advertise `arduinoq.local`.

### Option 3: Client Hosts File (Local Workaround)
**Effort: Low, per-client**

Add a static entry to each client machine's hosts file:

**Windows** (`C:\Windows\System32\drivers\etc\hosts`):
```
192.168.30.223  arduinoq arduinoq.local
```

**Linux/macOS** (`/etc/hosts`):
```
192.168.30.223  arduinoq arduinoq.local
```

This works without any mDNS support but must be done on each client.

### Option 4: Router DNS Entry (Network-Wide)
**Effort: Low, network-wide**

Most routers allow custom DNS entries. Add:
- Hostname: `arduinoq`
- IP: `192.168.30.223`

All devices on the network can then use `http://arduinoq:7000`.

### Option 5: Static IP + DHCP Reservation (Recommended Addition)
**Effort: Low**

Ensure the Arduino UNO Q always gets the same IP by setting a DHCP reservation in your router for its MAC address. This makes Options 2-4 reliable long-term.

## Recommendation

**Simplest path:**

1. First, verify if mDNS already works: try `http://unoq.local:7000` in your browser
2. If that fails, check the device's hostname via SSH and try `http://<actual-hostname>.local:7000`
3. If mDNS still doesn't work (common on Windows), use **Option 3** (hosts file) for immediate results
4. For a more permanent solution, use **Option 2** (set device hostname) combined with **Option 5** (DHCP reservation)

## What Changes to Our App Code?

**None required.** The application is already correctly:
- Binding to `0.0.0.0:7000` (accepts connections from any hostname)
- Referencing mDNS fallbacks in the video capture code

The only potential code change would be updating the CLAUDE.md documentation to reflect whatever hostname the user chooses.

## Investigation Results

- **mDNS resolution**: `unoq.local` does not resolve from client
- **Client OS**: Windows (requires Bonjour for `.local` resolution)
- **Device hostname**: `arduino-q`

The actual mDNS name should be `arduino-q.local`, not `unoq.local`.

## Recommended Solution

### Option A: Install Bonjour on Windows (Cleanest)

1. Download and install [Bonjour Print Services for Windows](https://support.apple.com/kb/DL999) from Apple
2. After install, try: `http://arduino-q.local:7000`

This enables proper mDNS support and will work for any `.local` device.

### Option B: Hosts File (Simplest, No Install)

Edit `C:\Windows\System32\drivers\etc\hosts` as Administrator and add:
```
192.168.30.223  arduino-q arduino-q.local
```

Then access: `http://arduino-q:7000` or `http://arduino-q.local:7000`

### Documentation Update

After confirming which approach works, update CLAUDE.md to reflect the correct hostname (`arduino-q.local` instead of `unoq.local`).
