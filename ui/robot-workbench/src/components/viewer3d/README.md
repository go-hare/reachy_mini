# 🤖 Viewer 3D - Reachy Mini

3D visualization module for Reachy Mini robot.

## 📁 Structure

```
viewer3d/
├── Viewer3D.jsx              # Main component with Canvas and UI
├── Scene.jsx                 # 3D scene (lights, environment, effects)
├── URDFRobot.jsx             # URDF model loading and animation
├── CinematicCamera.jsx       # Animated camera for scan view
├── SettingsOverlay.jsx       # Settings panel overlay
├── effects/                  # Visual effects
│   ├── ScanEffect.jsx        # Progressive scan effect
│   ├── PremiumScanEffect.jsx # Premium world-class scan effect
│   ├── ErrorHighlight.jsx    # Error mesh highlighting
│   ├── ParticleEffect.jsx    # Particle effects (sleep, love, etc.)
│   └── particles/
│       └── NoiseGenerator.js # Noise generation for particles
├── settings/                 # Settings panel components
│   ├── SettingsAppearanceCard.jsx  # Dark mode, display settings
│   ├── SettingsCacheCard.jsx       # Cache management
│   ├── SettingsUpdateCard.jsx      # App updates
│   ├── SettingsWifiCard.jsx        # WiFi configuration
│   └── ChangeWifiOverlay.jsx       # WiFi change dialog
├── hooks/
│   └── useRobotWebSocket.js  # Reads robot state from centralized store
└── index.js                  # Public module exports

Utils:
- src/utils/viewer3d/materials.js  # X-ray material creation
- src/utils/arraysEqual.js         # Array comparison with tolerance
```

## 🎯 Main Components

### `RobotViewer3D`

- Entry point of 3D viewer
- Manages UI (Settings button, Status tag, FPS meter)
- Props: `isActive`, `initialMode`, `hideControls`, `showScanEffect`, etc.

### `Scene`

- 3D scene configuration
- 3-point lighting (key, fill, rim)
- Fog for fade-out effect
- Grid floor (adapts to dark mode)

### `URDFRobot`

- URDF model loading from cache
- X-ray material system
- Real-time animation via joints (head, antennas, body)

## 🔧 Custom Hooks

### `useRobotWebSocket(isActive)`

Hook that reads robot state from the centralized Zustand store.

> **Note**: This hook no longer maintains its own WebSocket connection.
> Robot state is streamed by `useRobotStateWebSocket` (in App.jsx) and stored in `robotStateFull`.
> This hook simply reads from the store for backward compatibility.

**Returns:**

```javascript
{
  headPose: Array(16),       // 4x4 head pose matrix
  headJoints: Array(7),      // [yaw_body, stewart_1..6]
  passiveJoints: Array(21),  // Stewart passive joints (from daemon or WASM fallback)
  yawBody: number,           // Body rotation
  antennas: [left, right],   // Antenna positions
  dataVersion: number,       // For memo optimization
}
```

**WASM Fallback**: When the daemon doesn't provide passive joints (e.g., USB mode with AnalyticalKinematics),
they are calculated locally using the Rust WASM module (`useKinematicsWasm`).

## 🎨 Material System

The `src/utils/viewer3d/materials.js` module provides:

- `xrayShader` - Fresnel-based X-ray shader with rim lighting
- `createXrayMaterial(color, options)` - Creates X-ray material with options:
  - `opacity` - Material transparency (default: 0.3)
  - `rimColor` - Rim highlight color
  - `rimIntensity` - Rim effect intensity (default: 0.6)
  - `scanMode` - Use green colors for scan effect

## 📡 Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                        App.jsx                              │
│  useRobotStateWebSocket(isActive)                          │
│         │                                                   │
│         ▼                                                   │
│   WebSocket /api/state/ws/full @ 20Hz                      │
│   (head_pose, head_joints, body_yaw, antennas, passive)    │
│         │                                                   │
│         ▼                                                   │
│   robotStateFull (Zustand Store)                           │
└─────────┬───────────────────────────────────────────────────┘
          │
          ▼
    Viewer3D.jsx
          │
          ▼
    useRobotWebSocket(isActive)  ← Reads from store
          │
          ▼ (if passive_joints === null)
    🦀 WASM calculates passive joints
          │
          ▼
    Scene.jsx → URDFRobot.jsx (renders 3D model)
```

## 🚀 Usage

```jsx
import Viewer3D from './viewer3d';

<Viewer3D
  isActive={daemonActive}
  initialMode="normal"
  hideControls={false}
  showScanEffect={false}
  usePremiumScan={false}
  backgroundColor="#e0e0e0"
/>;
```

## ⚡ Performance

- **Single WebSocket**: All robot data streamed via one connection at 20Hz
- **Memoization**: Scene and URDFRobot use `dataVersion` for efficient updates
- **Object reuse**: Vector3/Matrix4 objects reused to avoid allocations
- **DPR limit**: Capped at 2x for GPU efficiency
- **WASM kinematics**: < 1ms for passive joint calculation when needed
