import { createContext, useContext, useMemo, useReducer, type ReactNode } from "react"
import {
  ControllerMode,
  createZeroAntennas,
  createZeroHeadPose,
  type ControllerAntennas,
  type ControllerHeadPose,
} from "@/components/controller/types"
import { TargetSmoothingManager } from "@/components/controller/utils/targetSmoothing"

interface ControllerState {
  mode: (typeof ControllerMode)[keyof typeof ControllerMode]
  headPose: ControllerHeadPose
  bodyYaw: number
  antennas: ControllerAntennas
  lastInteractionTime: number
  lastDragEndTime: number
  canSyncFromRobot: boolean
}

type ControllerPayload = Partial<{
  headPose: ControllerHeadPose
  bodyYaw: number
  antennas: ControllerAntennas
}>

type ControllerAction =
  | { type: "START_MOUSE_DRAG" }
  | { type: "START_GAMEPAD_INPUT" }
  | { type: "END_INTERACTION" }
  | { type: "START_RESET" }
  | { type: "UPDATE_HEAD_POSE"; payload: Partial<ControllerHeadPose> }
  | { type: "UPDATE_BODY_YAW"; payload: number }
  | { type: "UPDATE_ANTENNAS"; payload: ControllerAntennas }
  | { type: "UPDATE_ALL"; payload: ControllerPayload }
  | { type: "SYNC_FROM_ROBOT"; payload: ControllerPayload }
  | { type: "RESET_TO_ZERO" }

function createInitialState(): ControllerState {
  return {
    mode: ControllerMode.IDLE,
    headPose: createZeroHeadPose(),
    bodyYaw: 0,
    antennas: createZeroAntennas(),
    lastInteractionTime: 0,
    lastDragEndTime: 0,
    canSyncFromRobot: true,
  }
}

function controllerReducer(state: ControllerState, action: ControllerAction): ControllerState {
  const now = Date.now()

  switch (action.type) {
    case "START_MOUSE_DRAG":
      return {
        ...state,
        mode: ControllerMode.DRAGGING_MOUSE,
        lastInteractionTime: now,
        canSyncFromRobot: false,
      }
    case "START_GAMEPAD_INPUT":
      return {
        ...state,
        mode: ControllerMode.DRAGGING_GAMEPAD,
        lastInteractionTime: now,
        canSyncFromRobot: false,
      }
    case "END_INTERACTION":
      return {
        ...state,
        mode: ControllerMode.IDLE,
        lastDragEndTime: now,
        canSyncFromRobot: false,
      }
    case "START_RESET":
      return {
        ...state,
        mode: ControllerMode.RESETTING,
        headPose: createZeroHeadPose(),
        bodyYaw: 0,
        antennas: createZeroAntennas(),
      }
    case "UPDATE_HEAD_POSE":
      return {
        ...state,
        headPose: { ...state.headPose, ...action.payload },
      }
    case "UPDATE_BODY_YAW":
      return {
        ...state,
        bodyYaw: action.payload,
      }
    case "UPDATE_ANTENNAS":
      return {
        ...state,
        antennas: action.payload,
      }
    case "UPDATE_ALL":
      return {
        ...state,
        headPose: action.payload.headPose ?? state.headPose,
        bodyYaw: action.payload.bodyYaw ?? state.bodyYaw,
        antennas: action.payload.antennas ?? state.antennas,
      }
    case "SYNC_FROM_ROBOT": {
      if (!state.canSyncFromRobot || state.mode !== ControllerMode.IDLE) {
        return state
      }

      const timeSinceInteraction = now - state.lastInteractionTime
      if (timeSinceInteraction < 30_000) {
        return state
      }

      return {
        ...state,
        headPose: action.payload.headPose ?? state.headPose,
        bodyYaw: action.payload.bodyYaw ?? state.bodyYaw,
        antennas: action.payload.antennas ?? state.antennas,
        canSyncFromRobot: true,
      }
    }
    case "RESET_TO_ZERO":
      return {
        ...state,
        mode: ControllerMode.IDLE,
        headPose: createZeroHeadPose(),
        bodyYaw: 0,
        antennas: createZeroAntennas(),
        canSyncFromRobot: true,
      }
    default:
      return state
  }
}

interface ControllerContextValue {
  state: ControllerState
  actions: {
    startMouseDrag: () => void
    startGamepadInput: () => void
    endInteraction: () => void
    startReset: () => void
    updateHeadPose: (pose: Partial<ControllerHeadPose>) => void
    updateBodyYaw: (yaw: number) => void
    updateAntennas: (antennas: ControllerAntennas) => void
    updateAll: (values: ControllerPayload) => void
    syncFromRobot: (values: ControllerPayload) => void
    resetToZero: () => void
  }
  smoother: TargetSmoothingManager
  isDragging: boolean
  isUsingGamepad: boolean
  isActive: boolean
}

const ControllerContext = createContext<ControllerContextValue | null>(null)

export function ControllerProvider({
  children,
  isActive,
}: {
  children: ReactNode
  isActive: boolean
}) {
  const [state, dispatch] = useReducer(controllerReducer, createInitialState())
  const smoother = useMemo(() => new TargetSmoothingManager(), [])

  const actions = useMemo(
    () => ({
      startMouseDrag: () => dispatch({ type: "START_MOUSE_DRAG" }),
      startGamepadInput: () => dispatch({ type: "START_GAMEPAD_INPUT" }),
      endInteraction: () => dispatch({ type: "END_INTERACTION" }),
      startReset: () => dispatch({ type: "START_RESET" }),
      updateHeadPose: (pose: Partial<ControllerHeadPose>) =>
        dispatch({ type: "UPDATE_HEAD_POSE", payload: pose }),
      updateBodyYaw: (yaw: number) => dispatch({ type: "UPDATE_BODY_YAW", payload: yaw }),
      updateAntennas: (antennas: ControllerAntennas) =>
        dispatch({ type: "UPDATE_ANTENNAS", payload: antennas }),
      updateAll: (values: ControllerPayload) => dispatch({ type: "UPDATE_ALL", payload: values }),
      syncFromRobot: (values: ControllerPayload) =>
        dispatch({ type: "SYNC_FROM_ROBOT", payload: values }),
      resetToZero: () => dispatch({ type: "RESET_TO_ZERO" }),
    }),
    []
  )

  const isDragging =
    state.mode === ControllerMode.DRAGGING_MOUSE || state.mode === ControllerMode.DRAGGING_GAMEPAD
  const isUsingGamepad = state.mode === ControllerMode.DRAGGING_GAMEPAD

  const value = useMemo(
    () => ({
      state,
      actions,
      smoother,
      isDragging,
      isUsingGamepad,
      isActive,
    }),
    [actions, isActive, isDragging, isUsingGamepad, smoother, state]
  )

  return <ControllerContext.Provider value={value}>{children}</ControllerContext.Provider>
}

export function useController() {
  const context = useContext(ControllerContext)

  if (!context) {
    throw new Error("useController must be used within a ControllerProvider")
  }

  return context
}
