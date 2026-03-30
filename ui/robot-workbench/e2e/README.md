# E2E Testing for Reachy Mini Desktop App

End-to-end tests that validate the application works correctly before release.

## Overview

These tests run automatically in the release workflow on GitHub Actions. They:

1. Build the package for each platform (.deb, .msi, .app)
2. Install it on the CI runner
3. Launch the app and run through the simulation mode flow
4. Run WebdriverIO tests to validate core functionality

**If E2E tests fail, the release is blocked.**

## Platform Support

| Platform | WebDriver | Status | Cost |
|----------|-----------|--------|------|
| **Linux** | webkit2gtk-driver + tauri-driver | ✅ Active | Free |
| **Windows** | msedgedriver + tauri-driver | ✅ Active | Free |
| **macOS** | CrabNebula WebDriver | ✅ Active | ~€9/month |

## Architecture

### Linux & Windows

```
┌─────────────────────────────────────────────────────────────┐
│                    GitHub Actions Runner                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │ Build pkg   │───▶│ Install pkg  │───▶│ Run E2E Tests │  │
│  └─────────────┘    └──────────────┘    └───────────────┘  │
│                                                │            │
│                                                ▼            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              tauri-driver (WebDriver proxy)          │   │
│  │  ┌─────────────────┐    ┌────────────────────────┐  │   │
│  │  │ Native Driver   │◀──▶│ Reachy Mini Control    │  │   │
│  │  │ (webkit/edge)   │    │ (simulation mode)      │  │   │
│  │  └─────────────────┘    └────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### macOS (CrabNebula)

```
┌─────────────────────────────────────────────────────────────┐
│                    GitHub Actions (macOS)                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              @crabnebula/tauri-driver                │   │
│  │  ┌─────────────────┐    ┌────────────────────────┐  │   │
│  │  │ test-runner-    │◀──▶│ CrabNebula WebDriver   │  │   │
│  │  │ backend (:3000) │    │ (WKWebView automation) │  │   │
│  │  └─────────────────┘    └────────────────────────┘  │   │
│  │           │                       │                  │   │
│  │           ▼                       ▼                  │   │
│  │  ┌─────────────────────────────────────────────┐    │   │
│  │  │         Reachy Mini Control.app             │    │   │
│  │  │         (with tauri-plugin-automation)      │    │   │
│  │  └─────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Test Scenarios

| Test | What it validates |
|------|-------------------|
| App launches | Build integrity, Tauri bundling |
| Window title | Frontend rendering works |
| Connection screen | UI components load |
| Simulation mode selection | User interaction flow |
| Daemon startup | Sidecar starts, IPC communication |
| UI responsiveness | App remains stable |

## Running Locally

### Linux

```bash
# Install dependencies
sudo apt install webkit2gtk-driver xvfb libwebkit2gtk-4.1-0 libportaudio2
cargo install tauri-driver --locked

# Build and install the .deb
yarn build:sidecar-linux
yarn tauri build
sudo dpkg -i src-tauri/target/x86_64-unknown-linux-gnu/release/bundle/deb/*.deb

# Run tests with virtual display
xvfb-run yarn test:e2e
```

### Windows

```powershell
# Install dependencies
cargo install --git https://github.com/chippers/msedgedriver-tool
msedgedriver-tool.exe  # Downloads matching Edge driver
cargo install tauri-driver --locked

# Build and install the .msi
yarn build:sidecar-windows
yarn tauri build
# Install the MSI manually or via msiexec

# Run tests
yarn test:e2e
```

### macOS

```bash
# Requires CrabNebula subscription
# Get API key from https://crabnebula.cloud

# Install dependencies
yarn install  # Includes @crabnebula/tauri-driver

# Build the app (needs tauri-plugin-automation)
yarn build:sidecar-macos
yarn tauri build

# Run tests with CrabNebula
CN_API_KEY=your_key_here yarn test:e2e
```

## Configuration

- **Config file**: `e2e/wdio.conf.js`
- **Test specs**: `e2e/specs/*.spec.js`
- **Environment variables**:
  - `E2E_APP_BINARY`: Override the app path
  - `CN_API_KEY`: CrabNebula API key (macOS only)

## GitHub Secrets Required

| Secret | Platform | Description |
|--------|----------|-------------|
| `CN_API_KEY` | macOS | CrabNebula API key for WebDriver |

> **Note**: If `CN_API_KEY` is not configured, macOS E2E tests are skipped (not failed).

## Troubleshooting

### Tests fail with "WebKitWebDriver not found" (Linux)

```bash
# Install webkit2gtk-driver
sudo apt install webkit2gtk-driver
which WebKitWebDriver
```

### Tests fail with "Display not found" (Linux)

```bash
# Run with xvfb
xvfb-run yarn test:e2e
```

### Tests fail with "CN_API_KEY not set" (macOS)

```bash
# Set the API key
export CN_API_KEY=your_key_here
yarn test:e2e
```

### msedgedriver version mismatch (Windows)

```powershell
# Re-download matching driver
cargo install --git https://github.com/chippers/msedgedriver-tool --force
msedgedriver-tool.exe
```

## Adding New Tests

Create a new spec file in `e2e/specs/`:

```javascript
// e2e/specs/my-feature.spec.js
describe('My Feature', () => {
  it('should do something', async () => {
    // Wait for app to be ready
    await browser.pause(3000);
    
    // Get page content
    const content = await browser.execute(() => document.body.innerText);
    
    // Interact with the app via WebDriver
    const element = await browser.$('#my-element');
    expect(await element.isExisting()).toBe(true);
  });
});
```

## CI/CD Integration

The E2E tests run directly after each platform build:

```yaml
# .github/workflows/release-unified.yml

jobs:
  build-and-release:
    strategy:
      matrix:
        include:
          - os: ubuntu-22.04    # → E2E tests .deb
          - os: windows-latest  # → E2E tests .msi
          - os: macos-latest    # → E2E tests .app (if CN_API_KEY set)
    
    steps:
      - Build app
      - Run E2E tests  # ← Blocks release if fails
      - Sign & notarize
      - Upload artifacts
```

## Cost Breakdown

| Component | Cost |
|-----------|------|
| Linux E2E (webkit2gtk-driver) | Free |
| Windows E2E (msedgedriver) | Free |
| macOS E2E (CrabNebula) | ~€9/month |
| **Total** | **~€9/month** |

> CrabNebula is required because Apple doesn't provide a WKWebView driver for macOS desktop apps. This is how the Tauri team monetizes their work.
