import { createContext, useContext, useReducer, useRef, useMemo, useCallback } from 'react';
import { TargetSmoothingManager } from '@utils/targetSmoothing';

// =============================================================================
// STATE MACHINE - Clear states for the controller
// =============================================================================

export const ControllerMode = {
  IDLE: 'idle',
  DRAGGING_MOUSE: 'dragging_mouse',
  DRAGGING_GAMEPAD: 'dragging_gamepad',
  RESETTING: 'resetting',
};

// =============================================================================
// INITIAL STATE
// =============================================================================

const createInitialState = () => ({
  mode: ControllerMode.IDLE,

  // Robot position values
  headPose: { x: 0, y: 0, z: 0, pitch: 0, yaw: 0, roll: 0 },
  bodyYaw: 0,
  antennas: [0, 0],

  // Timestamps for interaction tracking
  lastInteractionTime: 0,
  lastDragEndTime: 0,

  // Sync state
  canSyncFromRobot: true,
});

// =============================================================================
// ACTION TYPES
// =============================================================================

const ActionTypes = {
  // Mode transitions
  START_MOUSE_DRAG: 'START_MOUSE_DRAG',
  START_GAMEPAD_INPUT: 'START_GAMEPAD_INPUT',
  END_INTERACTION: 'END_INTERACTION',
  START_RESET: 'START_RESET',

  // Value updates
  UPDATE_HEAD_POSE: 'UPDATE_HEAD_POSE',
  UPDATE_BODY_YAW: 'UPDATE_BODY_YAW',
  UPDATE_ANTENNAS: 'UPDATE_ANTENNAS',
  UPDATE_ALL: 'UPDATE_ALL',

  // Sync
  SYNC_FROM_ROBOT: 'SYNC_FROM_ROBOT',
  RESET_TO_ZERO: 'RESET_TO_ZERO',
};

// =============================================================================
// REDUCER
// =============================================================================

function controllerReducer(state, action) {
  const now = Date.now();

  switch (action.type) {
    // --- Mode transitions ---
    case ActionTypes.START_MOUSE_DRAG:
      return {
        ...state,
        mode: ControllerMode.DRAGGING_MOUSE,
        lastInteractionTime: now,
        canSyncFromRobot: false,
      };

    case ActionTypes.START_GAMEPAD_INPUT:
      return {
        ...state,
        mode: ControllerMode.DRAGGING_GAMEPAD,
        lastInteractionTime: now,
        canSyncFromRobot: false,
      };

    case ActionTypes.END_INTERACTION:
      return {
        ...state,
        mode: ControllerMode.IDLE,
        lastDragEndTime: now,
        // Don't allow sync for 30s after user interaction
        canSyncFromRobot: false,
      };

    case ActionTypes.START_RESET:
      return {
        ...state,
        mode: ControllerMode.RESETTING,
        headPose: { x: 0, y: 0, z: 0, pitch: 0, yaw: 0, roll: 0 },
        bodyYaw: 0,
        antennas: [0, 0],
      };

    // --- Value updates ---
    case ActionTypes.UPDATE_HEAD_POSE:
      return {
        ...state,
        headPose: { ...state.headPose, ...action.payload },
      };

    case ActionTypes.UPDATE_BODY_YAW:
      return {
        ...state,
        bodyYaw: action.payload,
      };

    case ActionTypes.UPDATE_ANTENNAS:
      return {
        ...state,
        antennas: action.payload,
      };

    case ActionTypes.UPDATE_ALL:
      return {
        ...state,
        headPose: action.payload.headPose ?? state.headPose,
        bodyYaw: action.payload.bodyYaw ?? state.bodyYaw,
        antennas: action.payload.antennas ?? state.antennas,
      };

    // --- Sync ---
    case ActionTypes.SYNC_FROM_ROBOT: {
      // Only sync if allowed and not interacting
      if (!state.canSyncFromRobot || state.mode !== ControllerMode.IDLE) {
        return state;
      }

      // Check if enough time has passed since last interaction (30s)
      const timeSinceInteraction = now - state.lastInteractionTime;
      if (timeSinceInteraction < 30000) {
        return state;
      }

      return {
        ...state,
        headPose: action.payload.headPose ?? state.headPose,
        bodyYaw: action.payload.bodyYaw ?? state.bodyYaw,
        antennas: action.payload.antennas ?? state.antennas,
        canSyncFromRobot: true,
      };
    }

    case ActionTypes.RESET_TO_ZERO:
      return {
        ...state,
        mode: ControllerMode.IDLE,
        headPose: { x: 0, y: 0, z: 0, pitch: 0, yaw: 0, roll: 0 },
        bodyYaw: 0,
        antennas: [0, 0],
        canSyncFromRobot: true,
      };

    default:
      return state;
  }
}

// =============================================================================
// CONTEXT
// =============================================================================

const ControllerContext = createContext(null);

export function ControllerProvider({ children, isActive }) {
  const [state, dispatch] = useReducer(controllerReducer, null, createInitialState);

  // Single source of truth for smoothing
  const smoother = useMemo(() => new TargetSmoothingManager(), []);

  // Expose actions as clean functions
  const actions = useMemo(
    () => ({
      startMouseDrag: () => dispatch({ type: ActionTypes.START_MOUSE_DRAG }),
      startGamepadInput: () => dispatch({ type: ActionTypes.START_GAMEPAD_INPUT }),
      endInteraction: () => dispatch({ type: ActionTypes.END_INTERACTION }),
      startReset: () => dispatch({ type: ActionTypes.START_RESET }),

      updateHeadPose: pose => dispatch({ type: ActionTypes.UPDATE_HEAD_POSE, payload: pose }),
      updateBodyYaw: yaw => dispatch({ type: ActionTypes.UPDATE_BODY_YAW, payload: yaw }),
      updateAntennas: antennas =>
        dispatch({ type: ActionTypes.UPDATE_ANTENNAS, payload: antennas }),
      updateAll: values => dispatch({ type: ActionTypes.UPDATE_ALL, payload: values }),

      syncFromRobot: values => dispatch({ type: ActionTypes.SYNC_FROM_ROBOT, payload: values }),
      resetToZero: () => dispatch({ type: ActionTypes.RESET_TO_ZERO }),
    }),
    []
  );

  // Derived state
  const isDragging =
    state.mode === ControllerMode.DRAGGING_MOUSE || state.mode === ControllerMode.DRAGGING_GAMEPAD;

  const isUsingGamepad = state.mode === ControllerMode.DRAGGING_GAMEPAD;

  const value = useMemo(
    () => ({
      state,
      actions,
      smoother,
      isDragging,
      isUsingGamepad,
      isActive,
    }),
    [state, actions, smoother, isDragging, isUsingGamepad, isActive]
  );

  return <ControllerContext.Provider value={value}>{children}</ControllerContext.Provider>;
}

// =============================================================================
// HOOK
// =============================================================================

export function useController() {
  const context = useContext(ControllerContext);
  if (!context) {
    throw new Error('useController must be used within a ControllerProvider');
  }
  return context;
}

export { ActionTypes };
