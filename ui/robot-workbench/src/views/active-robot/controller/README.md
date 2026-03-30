# Controller Module

Module for controlling the position and orientation of the Reachy Mini robot.

## 📁 Structure

```
controller/
├── Controller.jsx                # Main component with ControllerProvider
├── context/
│   ├── ControllerContext.jsx     # State machine + Provider
│   └── index.js                  # Context exports
├── hooks/
│   ├── useControllerHandlers.js  # Unified UI handlers (mouse/touch)
│   ├── useControllerInput.js     # Gamepad/keyboard input processing
│   ├── useControllerSync.js      # Robot state synchronization
│   ├── useControllerSmoothing.js # Smoothing loop (60fps)
│   ├── useControllerAPI.js       # HTTP API communication
│   └── index.js                  # Hook exports
├── components/
│   ├── Joystick2D.jsx            # 2D joystick control
│   ├── VerticalSlider.jsx        # Vertical slider (Position Z)
│   ├── SimpleSlider.jsx          # Horizontal slider (Roll)
│   ├── CircularSlider.jsx        # Circular slider (Antennas, Body Yaw)
│   └── index.js                  # Component exports
├── utils/
│   ├── formatPose.js             # Pose formatting
│   └── intelligentLogging.js     # Throttled logging
└── index.js                      # Main export
```

## 🎯 Architecture

### State Machine

The controller uses a state machine for clear mode management:

```javascript
const ControllerMode = {
  IDLE: 'idle', // No interaction
  DRAGGING_MOUSE: 'dragging_mouse', // Mouse/touch interaction
  DRAGGING_GAMEPAD: 'dragging_gamepad', // Gamepad input
  RESETTING: 'resetting', // Reset animation
};
```

### Data Flow

```
        User Input
       /          \
  Mouse/Touch    Gamepad/Keyboard
      ↓               ↓
useControllerHandlers  useControllerInput
      \               /
       ↓             ↓
  ControllerContext (state machine)
             ↓
  TargetSmoothingManager (interpolation)
             ↓
  useControllerSmoothing (60fps loop)
             ↓
  useControllerAPI (throttled 50ms)
             ↓
  WebSocket → ws://.../api/move/ws/set_target
             ↓
          Daemon
```

### Context Provider

```jsx
<ControllerProvider isActive={isActive}>
  <ControllerInner ... />
</ControllerProvider>
```

The context provides:

- `state`: Current controller state (mode, values, timestamps)
- `actions`: State transition functions
- `smoother`: TargetSmoothingManager instance
- `isDragging`: Derived state
- `isActive`: From props

## 🔧 Usage

```jsx
import Controller from '@views/active-robot/controller';

<Controller
  isActive={isActive}
  darkMode={darkMode}
  onResetReady={handleResetReady}
  onIsAtInitialPosition={handleIsAtInitialPosition}
/>;
```

## 📦 Exports

```javascript
// Main component
import Controller from '@views/active-robot/controller';

// Context
import {
  ControllerProvider,
  useController,
  ControllerMode,
} from '@views/active-robot/controller/context';

// Hooks
import {
  useControllerHandlers,
  useControllerInput,
  useControllerSync,
  useControllerSmoothing,
  useControllerAPI,
} from '@views/active-robot/controller/hooks';

// Components
import {
  Joystick2D,
  VerticalSlider,
  SimpleSlider,
  CircularSlider,
} from '@views/active-robot/controller/components';
```

## ⚡ Performance

- **WebSocket streaming**: Persistent connection, ~2-5ms latency
- **Throttled commands**: ~20fps (50ms) to avoid flooding daemon
- **UI updates**: Throttled to 15fps for React performance
- **Smoothing loop**: 60fps with requestAnimationFrame
- **State machine**: O(1) mode checks, no complex condition trees
- **Single source of truth**: Context eliminates state sync bugs
- **Auto-reconnect**: WebSocket reconnects automatically on disconnect
