# Discovery System Improvement Plan

> **Date**: February 2026
> **Status**: Proposal
> **Scope**: `src-tauri/src/discovery/mod.rs`, `src-tauri/src/network/mod.rs`, `src/hooks/system/useRobotDiscovery.js`

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current Architecture](#current-architecture)
3. [Observed Problems](#observed-problems)
4. [State of the Art](#state-of-the-art)
5. [Improvement Plan](#improvement-plan)
6. [VPN Deep Dive](#vpn-deep-dive)
7. [Implementation Details](#implementation-details)
8. [Dependency Audit](#dependency-audit)
9. [Summary](#summary)

---

## Executive Summary

The current robot discovery system uses a sequential cascade (cache → static peers → mDNS → manual fallback). While functional in the happy path, it suffers from:

- **Concurrent scan races** (no backend mutex)
- **13s worst-case latency** (sequential timeouts)
- **No persistence** (cache lost on restart)
- **VPN blindness** (mDNS and DNS fail silently through VPN tunnels)
- **Generic mDNS service type** (`_http._tcp.local.` instead of a dedicated `_reachy._tcp.local.`)
- **No subnet scan fallback**

This plan addresses all issues in 4 phases, requiring only **1 new dependency** (`tauri-plugin-store`, official Tauri plugin) and leveraging existing APIs from `tokio`, `reqwest`, and `mdns-sd`.

---

## Current Architecture

### Discovery Flow (sequential cascade)

```
┌──────────────────────────────────────────────────────────┐
│                    discover_robots()                      │
│                                                          │
│  1. Cache (last_known_ip)          timeout: 2s           │
│     └─ Hit? → return immediately                         │
│     └─ Miss? → continue                                  │
│                                                          │
│  2. Static Peers (sequential)      timeout: 3s/peer      │
│     ├─ reachy-mini.home                                  │
│     ├─ reachy-mini.local                                 │
│     └─ 192.168.1.18                                      │
│     └─ First hit? → update cache, return                 │
│     └─ All miss? → continue                              │
│                                                          │
│  3. mDNS browse _http._tcp.local.  timeout: 5s           │
│     └─ Filter by name containing "reachy"                │
│     └─ Found? → update cache, return                     │
│     └─ Not found? → return empty                         │
│                                                          │
│  4. Manual IP (user input)         timeout: 5s           │
│     └─ Last resort, not automatic                        │
└──────────────────────────────────────────────────────────┘

Frontend polling: every 3s (fixed interval, no backoff)
Worst-case cycle: ~13s (2s + 0s + 3s + 3s + 5s)
```

### Key Files

| File | Role |
|------|------|
| `src-tauri/src/discovery/mod.rs` | Rust backend: cache, static peers, mDNS, `connect_to_ip` |
| `src-tauri/src/network/mod.rs` | VPN detection via network interface name |
| `src/hooks/system/useRobotDiscovery.js` | Frontend hook: USB + WiFi scan loop |
| `src/hooks/system/useRobotDiscoveryV2.js` | V2 variant with VPN awareness |
| `src/config/daemon.js` | Polling intervals (`USB_CHECK: 3s`) |

### Timeout Configuration

| Context | Value | Location |
|---------|-------|----------|
| Cache check | 2s | `discovery/mod.rs:178` |
| Static peer check | 3s per peer | `discovery/mod.rs:197` |
| mDNS browse | 5s | `discovery/mod.rs:218` |
| Manual connection | 5s | `discovery/mod.rs:258` |
| Frontend poll | 3s (fixed) | `daemon.js` |

---

## Observed Problems

### 1. Concurrent Scans (Critical)

After HMR reload or rapid navigation, multiple `discover_robots` calls run in parallel with no backend mutex. Observed in logs:

```
[discovery] 🚀 Starting robot discovery
[discovery] 📦 Checking cached IP: reachy-mini.local
[discovery] 🚀 Starting robot discovery          ← 2nd parallel scan
[discovery] 📦 Checking cached IP: reachy-mini.local
```

Results interleave, both cascade independently, doubling network pressure.

### 2. Cache/Peer Duplication

When cache contains `reachy-mini.local`, the same hostname is retested in static peers (3s extra timeout for nothing).

### 3. Fixed Polling Without Backoff

When the robot is off, the app generates a continuous stream of failed requests every 3s with no backoff.

### 4. No Persistence

Cache (`last_known_ip`) and manually added static peers are in-memory only. Every app restart triggers a full cold discovery.

### 5. Sequential Discovery

Cache → peers → mDNS runs sequentially. If cache misses (2s), and DNS-based peers timeout (3s + 3s), 8s elapse before mDNS even starts.

### 6. mDNS Never Finds Anything

In all observed logs, mDNS returns 0 results. The generic `_http._tcp.local.` service type catches all HTTP services and filters by name — the robot may not advertise this service type correctly.

### 7. VPN Breaks Everything Silently

With a VPN active, mDNS multicast goes through the tunnel, DNS queries go to the VPN DNS server, and even direct IPs may route through the tunnel. All 3 automatic discovery methods fail.

---

## State of the Art

### Industry Comparison

| Feature | Home Assistant | Bambu Studio | Spotify Connect | OctoPrint | **Current** |
|---------|---------------|-------------|-----------------|-----------|-------------|
| mDNS/DNS-SD | Dedicated type | No | Dedicated type | Dedicated type | **Generic** `_http._tcp` |
| SSDP/UPnP | Yes | Yes (primary) | No | Yes | **No** |
| Subnet scan | Via integrations | No | No | No | **No** |
| Parallel discovery | Yes | N/A (passive) | Yes | N/A | **No (cascade)** |
| Disk persistence | Yes | Yes | Yes | Yes | **No (memory)** |
| Exponential backoff | Yes | N/A | Yes | N/A | **No (fixed 3s)** |
| Manual IP | Yes | No | No | Yes | **Yes** |
| VPN detection | No | No | No | No | **Yes** (advantage) |
| USB discovery | N/A | N/A | N/A | Yes | **Yes** |
| Scan dedup mutex | Yes | N/A | Yes | N/A | **No** |

### Reference Implementations

**Home Assistant** uses simultaneous multi-protocol discovery (mDNS + SSDP in parallel). Connections are persisted in SQLite. Discovery adapts based on network interface selection.
- Source: [developers.home-assistant.io/docs/network_discovery](https://developers.home-assistant.io/docs/network_discovery)

**Spotify Connect** devices register a dedicated mDNS service type `_spotify-connect._tcp.local.` per RFC 6762/6763. The app browses only that type — no generic filtering.
- Source: [developer.spotify.com/documentation/commercial-hardware/implementation/guides/zeroconf](https://developer.spotify.com/documentation/commercial-hardware/implementation/guides/zeroconf)

**OctoPrint** advertises itself via Zeroconf with service type `_octoprint._tcp.local.` and supports per-interface configuration.
- Source: [docs.octoprint.org/en/master/bundledplugins/discovery.html](https://docs.octoprint.org/en/master/bundledplugins/discovery.html)

**gRPC** defines the industry-standard exponential backoff algorithm: `INITIAL_BACKOFF=1s, MULTIPLIER=1.6, MAX_BACKOFF=120s, JITTER=±20%`.
- Source: [grpc.github.io/grpc/core/md_doc_connection-backoff.html](https://grpc.github.io/grpc/core/md_doc_connection-backoff.html)
- Reference implementation: [grpc-go/internal/backoff/backoff.go](https://github.com/grpc/grpc-go/blob/master/internal/backoff/backoff.go)

### Evaluated and Rejected

**`tauri-plugin-network`** (community plugin for subnet scanning):
- 131 GitHub stars, 85 npm downloads/week, 1 maintainer
- Open security advisory since 2023 (`RUSTSEC-2023-0019: kuchiki is unmaintained`)
- ICMP scan, packet sniffing marked as "not implemented"
- **Verdict**: Not production-ready. Implement subnet scan natively with `tokio` + `reqwest` instead.

---

## Improvement Plan

### Phase 1 — Foundations (no new dependencies)

| ID | Change | Effort | Impact |
|----|--------|--------|--------|
| 1.1 | Mutex to prevent concurrent scans | 30 min | High |
| 1.2 | Deduplicate cache and static peers | 15 min | Medium |
| 1.3 | Exponential backoff with jitter (frontend) | 45 min | Medium |

### Phase 2 — Persistence & Parallelization

| ID | Change | Effort | Impact |
|----|--------|--------|--------|
| 2.1 | Persist cache to disk (`tauri-plugin-store`) | 1h | High |
| 2.2 | Parallel discovery with `tokio::select!` | 2h | High |

### Phase 3 — VPN Resilience & Subnet Scan

| ID | Change | Effort | Impact |
|----|--------|--------|--------|
| 3.1 | Force mDNS on physical interface (`mdns-sd` native API) | 1h | High |
| 3.2 | Force HTTP requests on local IP (`reqwest::local_address`) | 1h | High |
| 3.3 | Subnet scan fallback (async /24 scan) | 2h | Medium |

### Phase 4 — Dedicated mDNS Service Type

| ID | Change | Effort | Impact |
|----|--------|--------|--------|
| 4.1 | Register `_reachy._tcp.local.` on the robot daemon | 3h | High |

**Total estimated effort**: ~12 hours
**New dependencies**: 1 (`tauri-plugin-store`, official Tauri plugin, 282K downloads/90 days)

---

## VPN Deep Dive

### Why VPN Breaks Discovery

A VPN redirects the default network interface to a tunnel (e.g., `utun4` on macOS). Three consequences:

#### 1. mDNS Multicast is Misrouted

mDNS sends packets to `224.0.0.251` (multicast). With a VPN, these packets go through the tunnel to a remote server instead of the local WiFi network. The robot never receives them.

```
Without VPN:  [Mac] --multicast--> [WiFi LAN] --> [Reachy] ✅
With VPN:     [Mac] --multicast--> [VPN Tunnel] --> [Remote Server] ❌
```

#### 2. DNS Resolution Fails for `.local` and `.home`

- `reachy-mini.local` is resolved via mDNS (same problem as above)
- `reachy-mini.home` is resolved by the router's DNS — but VPN redirects DNS queries to the VPN provider's DNS server, which doesn't know the local router

```
Without VPN:  DNS "reachy-mini.home" --> [Router] --> "192.168.1.18" ✅
With VPN:     DNS "reachy-mini.home" --> [VPN DNS] --> NXDOMAIN ❌
```

#### 3. Direct IP Routing May Fail

With a "full tunnel" VPN (default for most consumer VPNs), even traffic to `192.168.x.x` goes through the tunnel. The VPN server can't route to the user's local network.

With "split tunnel" VPN, local traffic stays local — direct IP may work.

### Current VPN Detection

The `network/mod.rs` module checks the default interface name for VPN patterns (`utun`, `tun`, `tap`, `tailscale`, etc.). Limitations:

- **False positives**: macOS creates `utun0`/`utun1` even without a VPN (system tunnels)
- **False negatives**: Some VPNs (Cisco AnyConnect) use non-standard interface names

### Proposed VPN Mitigation

Rather than just detecting VPN and showing a warning, actively work around it:

1. **`mdns-sd::ServiceDaemon::enable_interface("en0")`** — Force mDNS on the physical WiFi interface
2. **`reqwest::ClientBuilder::local_address(local_ip)`** — Bind HTTP requests to the LAN IP
3. **Identify physical interface IP** — Use `default-net` to find IPs in private ranges (192.168.x.x, 10.x.x.x, 172.16-31.x.x)

---

## Implementation Details

### 1.1 — Mutex Anti-Concurrent Scans

Uses `tokio::sync::Mutex` (already in dependencies).

```rust
pub struct DiscoveryState {
    pub last_known_ip: Arc<RwLock<Option<String>>>,
    pub static_peers: Arc<RwLock<Vec<String>>>,
    pub is_scanning: Arc<tokio::sync::Mutex<()>>,
}

#[tauri::command]
pub async fn discover_robots(
    state: tauri::State<'_, DiscoveryState>,
) -> Result<Vec<RobotInfo>, String> {
    let _guard = match state.is_scanning.try_lock() {
        Ok(guard) => guard,
        Err(_) => {
            log::info!("[discovery] Scan already in progress, skipping");
            return Ok(vec![]);
        }
    };
    // ... rest of discovery logic
}
```

**Reference**: [tokio::sync::Mutex](https://docs.rs/tokio/latest/tokio/sync/struct.Mutex.html)

### 1.3 — Exponential Backoff (Frontend)

Follows the gRPC backoff specification, adapted for LAN discovery.

```javascript
// Constants (adapted from gRPC spec for LAN context)
const INITIAL_BACKOFF_MS = 3000;
const MULTIPLIER = 1.6;
const MAX_BACKOFF_MS = 30000;
const JITTER = 0.2;

let currentBackoff = INITIAL_BACKOFF_MS;

function getNextInterval(robotFound) {
  if (robotFound) {
    currentBackoff = INITIAL_BACKOFF_MS;
    return INITIAL_BACKOFF_MS;
  }
  currentBackoff = Math.min(currentBackoff * MULTIPLIER, MAX_BACKOFF_MS);
  const jitter = currentBackoff * JITTER * (Math.random() * 2 - 1);
  return Math.round(currentBackoff + jitter);
}

// Progression: 3s → 4.8s → 7.7s → 12.3s → 19.7s → 30s (cap)
```

**Reference**: [gRPC Connection Backoff Spec](https://grpc.github.io/grpc/core/md_doc_connection-backoff.html)

### 2.1 — Disk Persistence

Uses `tauri-plugin-store` (official Tauri plugin, v2.4.2, 282K downloads/90 days).

```rust
// Save on successful discovery
let store = app.store("discovery.json")?;
store.set("last_known_ip", json!(ip));
store.set("static_peers", json!(peers));
store.save()?;

// Restore on startup
let store = app.store("discovery.json")?;
if let Some(ip) = store.get("last_known_ip") {
    *state.last_known_ip.write().await = serde_json::from_value(ip).ok();
}
```

**Reference**: [tauri.app/plugin/store](https://tauri.app/plugin/store)

### 2.2 — Parallel Discovery

Uses `tokio::select!` (already in dependencies).

```rust
tokio::select! {
    biased; // Try cache first (cheapest)

    result = check_cache(&state, port) => {
        if let Ok(robot) = result { return Ok(vec![robot]); }
    }
    result = check_all_static_peers(&state, port) => {
        if let Ok(robots) = result {
            if !robots.is_empty() { return Ok(robots); }
        }
    }
    result = discover_via_mdns(Duration::from_secs(5)) => {
        if let Ok(robots) = result {
            if !robots.is_empty() { return Ok(robots); }
        }
    }
    _ = tokio::time::sleep(Duration::from_secs(8)) => {
        log::warn!("[discovery] Global timeout reached");
    }
}
```

**Reference**: [tokio::select! macro](https://docs.rs/tokio/latest/tokio/macro.select.html)

### 3.1 — mDNS on Physical Interface

Uses `mdns-sd` native API (`enable_interface` / `disable_interface`).

```rust
let mdns = ServiceDaemon::new()?;

// Disable VPN tunnel interfaces
for iface in ["utun", "tun", "tap", "wg", "tailscale", "nordvpn"] {
    let _ = mdns.disable_interface(iface);
}

// Enable known physical interfaces
#[cfg(target_os = "macos")]
let _ = mdns.enable_interface("en0"); // macOS WiFi

#[cfg(target_os = "linux")]
let _ = mdns.enable_interface("wlan0"); // Linux WiFi
```

**Reference**: [mdns-sd ServiceDaemon::enable_interface](https://docs.rs/mdns-sd/latest/mdns_sd/struct.ServiceDaemon.html#method.enable_interface)

### 3.2 — HTTP Bind on Local IP

Uses `reqwest::ClientBuilder::local_address` (available in reqwest 0.11, already in Cargo.toml).

```rust
fn find_lan_ip() -> Option<IpAddr> {
    let iface = default_net::get_default_interface().ok()?;
    iface.ipv4.iter()
        .map(|ip| IpAddr::V4(ip.addr))
        .find(|ip| match ip {
            IpAddr::V4(v4) => {
                let o = v4.octets();
                // Private ranges: 192.168.x.x, 10.x.x.x, 172.16-31.x.x
                o[0] == 192 || o[0] == 10
                    || (o[0] == 172 && o[1] >= 16 && o[1] <= 31)
            }
            _ => false,
        })
}

async fn check_robot_at_ip(ip: &str, port: u16, timeout_secs: u64) -> Result<RobotInfo, String> {
    let mut builder = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_secs));

    if let Some(local_ip) = find_lan_ip() {
        builder = builder.local_address(local_ip);
    }

    let client = builder.build().map_err(|e| format!("HTTP client error: {}", e))?;
    // ... rest unchanged
}
```

**Reference**: [reqwest::ClientBuilder::local_address](https://docs.rs/reqwest/0.11.27/reqwest/struct.ClientBuilder.html#method.local_address)

### 3.3 — Subnet Scan

No new dependency. Uses `tokio::sync::Semaphore` for concurrency control (pattern from RustScan).

```rust
async fn scan_subnet(port: u16, timeout: Duration) -> Vec<RobotInfo> {
    let local_ip = find_lan_ip();
    let subnet = match local_ip {
        Some(IpAddr::V4(v4)) => {
            let o = v4.octets();
            format!("{}.{}.{}", o[0], o[1], o[2])
        }
        _ => return vec![],
    };

    let semaphore = Arc::new(tokio::sync::Semaphore::new(50));
    let mut tasks = Vec::new();

    for i in 1..=254 {
        let ip = format!("{}.{}", subnet, i);
        let sem = semaphore.clone();
        tasks.push(tokio::spawn(async move {
            let _permit = sem.acquire().await.ok()?;
            check_robot_at_ip(&ip, port, timeout.as_secs()).await.ok()
        }));
    }

    futures_util::future::join_all(tasks).await
        .into_iter()
        .filter_map(|r| r.ok().flatten())
        .collect()
}
```

**Reference**: [RustScan](https://github.com/RustScan/RustScan) (semaphore-limited concurrent scanning pattern)

### 4.1 — Dedicated mDNS Service Type

**Robot side** (Python daemon):

```python
from zeroconf import ServiceInfo, Zeroconf
import socket

info = ServiceInfo(
    "_reachy._tcp.local.",
    "Reachy Mini._reachy._tcp.local.",
    addresses=[socket.inet_aton(local_ip)],
    port=8000,
    properties={"version": "0.9.19", "serial": serial_number},
)
zeroconf = Zeroconf()
zeroconf.register_service(info)
```

**App side** (Rust):

```rust
let receiver = mdns.browse("_reachy._tcp.local.")?;
// No more name filtering needed — only Reachy robots announce this service type
```

**Reference**: Pattern used by [Spotify Connect](https://developer.spotify.com/documentation/commercial-hardware/implementation/guides/zeroconf) (`_spotify-connect._tcp.`), [Home Assistant](https://home-assistant.io/integrations/zeroconf) (`_home-assistant._tcp.`), [OctoPrint](https://docs.octoprint.org/en/master/bundledplugins/discovery.html) (`_octoprint._tcp.`). Compliant with RFC 6762/6763.

---

## Dependency Audit

### Existing Dependencies (no changes needed)

| Crate | Version | Used For |
|-------|---------|----------|
| `tokio` | 1.x | `Mutex`, `select!`, `Semaphore`, `sleep` |
| `reqwest` | 0.11 | `local_address()` for VPN bypass |
| `mdns-sd` | 0.11 | `enable_interface()` / `disable_interface()` for VPN bypass |
| `default-net` | 0.22 | Finding physical interface IP |
| `futures-util` | 0.3 | `join_all` for subnet scan |

### New Dependency

| Crate | Version | Downloads (90d) | Maintainer | Purpose |
|-------|---------|-----------------|------------|---------|
| `tauri-plugin-store` | 2.x | 282,000 | Tauri team (official) | Persistent key-value cache |

### Evaluated and Rejected

| Crate | Reason for Rejection |
|-------|---------------------|
| `tauri-plugin-network` | 85 npm downloads/week, 1 maintainer, open RUSTSEC-2023-0019, incomplete features |
| `zeroconf` | Lower docs coverage (72%), wraps system libs (Bonjour/Avahi), less portable than `mdns-sd` |

---

## Summary

### Target Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                    discover_robots() v2                        │
│                                                               │
│  ┌─ Mutex guard (skip if scan already running) ──────────┐   │
│  │                                                        │   │
│  │  tokio::select! {                                      │   │
│  │    ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │   │
│  │    │ Cache (2s)  │  │ Peers (3s)   │  │ mDNS (5s)  │  │   │
│  │    │ from disk   │  │ deduped      │  │ _reachy._  │  │   │
│  │    │ + memory    │  │ vs cache     │  │ tcp.local. │  │   │
│  │    └──────┬──────┘  └──────┬───────┘  └─────┬──────┘  │   │
│  │           └────────────┬───┘                │          │   │
│  │                   First wins ◄──────────────┘          │   │
│  │                        │                               │   │
│  │              ┌─────────▼──────────┐                    │   │
│  │              │ All failed?        │                    │   │
│  │              │ → Subnet scan /24  │                    │   │
│  │              │   (50 concurrent)  │                    │   │
│  │              └────────────────────┘                    │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                               │
│  VPN active?                                                  │
│  → mDNS forced on en0/wlan0 (skip tunnel interfaces)         │
│  → HTTP bound to LAN IP via reqwest::local_address            │
│                                                               │
│  On success → save to disk (tauri-plugin-store)               │
│  On failure → exponential backoff (gRPC spec: 3s→30s)         │
└───────────────────────────────────────────────────────────────┘
```

### Expected Improvements

| Metric | Before | After |
|--------|--------|-------|
| Worst-case discovery time | ~13s | ~5s (parallel) |
| Cold start (after restart) | Full cascade | Cache from disk (~2s) |
| VPN active | All methods fail | mDNS + HTTP bypass tunnel |
| Robot off (polling pressure) | 20 requests/min | 2-4 requests/min (backoff) |
| Concurrent scan races | Possible | Impossible (mutex) |
| Duplicate peer checks | Yes (cache + peers overlap) | No (deduped) |

### Effort Breakdown

| Phase | Description | Effort | New Deps |
|-------|-------------|--------|----------|
| Phase 1 | Mutex + dedup + backoff | ~1.5h | 0 |
| Phase 2 | Persistence + parallel discovery | ~3h | 1 (official) |
| Phase 3 | VPN resilience + subnet scan | ~4h | 0 |
| Phase 4 | Dedicated mDNS service type | ~3h | 0 (robot-side change) |
| **Total** | | **~12h** | **1** |
