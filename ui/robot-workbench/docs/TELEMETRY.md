# Telemetry - Reachy Mini Control

## Overview

Reachy Mini Control collects **anonymous usage data** to improve user experience and product quality.

## 🔒 Privacy Commitment

### What We Collect

- **OS Type**: macOS, Windows, Linux
- **App Version**: e.g., 0.9.5
- **Usage Events**: Features used, interface actions
- **Errors and Crashes**: To detect and fix bugs
- **Session Duration**: App usage time

### What We DO NOT Collect

- ❌ **No personal data** (name, email, address)
- ❌ **No identifying IP address**
- ❌ **No location data**
- ❌ **No session content** with the robot
- ❌ **No video or images** from camera
- ❌ **No sensitive data**

## 🌍 Hosting and Compliance

- **Hosting**: PostHog EU Cloud (servers in the European Union)
- **GDPR**: Compliant with General Data Protection Regulation
- **Anonymization**: All data is aggregated and anonymized

## ⚙️ Managing Your Preferences

### Disable Telemetry

1. Open **Settings** (⚙️)
2. Go to **Privacy & Data** section
3. Toggle off **"Share anonymous usage data"**

Your choice is **immediately respected** and saved locally.

### Default Behavior

Telemetry is **enabled by default** (opt-out approach) because:
- Data is **fully anonymous**
- It helps us **improve the product**
- You can **disable it** anytime

## 📊 Events Collected

### Session & Connection
- `app_started`: App opened
- `app_closed`: App closed (with session duration)
- `robot_connected`: Robot connected (USB/WiFi/Simulation)
- `robot_disconnected`: Robot disconnected
- `connection_error`: Connection errors

### Feature Usage
- `controller_used`: Controller type used (gamepad, keyboard, joystick)
- `expression_played`: Expression played (emotion or dance)
- `robot_wake_up` / `robot_go_to_sleep`: Robot state control

### App Store
- `hf_app_installed` / `hf_app_uninstalled`: HuggingFace app installation/uninstallation
- `hf_app_started` / `hf_app_stopped`: App launch/stop (with duration)
- `discover_opened`: App catalog opened

### WiFi Configuration
- `wifi_setup_started` / `wifi_setup_completed`: WiFi setup (with success/failure status)

### Interface
- `camera_feed_viewed`: Camera feed opened
- `settings_opened`: Settings opened
- `dark_mode_toggled`: Theme changed

## 🎯 Why This Data?

### Experience Improvement
- **Identify bugs**: Detect crashes and errors
- **Prioritize features**: Know what's being used
- **Optimize performance**: Understand friction points

### Concrete Examples
- If 80% of users use gamepad → we improve this feature
- If an expression crashes often → we fix the bug with priority
- If nobody uses a feature → we redesign or remove it

## 🔍 Transparency

### Open Source Code
Telemetry code is **open source**:
- `/src/utils/telemetry/index.js`: Implementation
- `/src/utils/telemetry/events.js`: Event list

### No Third-Party Tracking
We use **no** third-party tracking services (Google Analytics, Facebook Pixel, etc.). Only PostHog EU.

## 📞 Contact

Questions about telemetry or privacy?
- Email: contact@pollen-robotics.com
- Website: https://pollen-robotics.com/privacy

## ⚖️ Legal Notice

In accordance with GDPR (Articles 13 and 21), you are informed of the collection of anonymous usage data and have the right to object (opt-out) in the application settings.

---

**Last updated**: January 15, 2026
