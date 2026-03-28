export interface ControllerHeadPose {
  x: number
  y: number
  z: number
  pitch: number
  yaw: number
  roll: number
}

export type ControllerAntennas = [number, number]

export interface ControllerValues {
  headPose: ControllerHeadPose
  bodyYaw: number
  antennas: ControllerAntennas
}

export const ControllerMode = {
  IDLE: "idle",
  DRAGGING_MOUSE: "dragging_mouse",
  DRAGGING_GAMEPAD: "dragging_gamepad",
  RESETTING: "resetting",
} as const

export type ControllerMode = (typeof ControllerMode)[keyof typeof ControllerMode]

export function createZeroHeadPose(): ControllerHeadPose {
  return {
    x: 0,
    y: 0,
    z: 0,
    pitch: 0,
    yaw: 0,
    roll: 0,
  }
}

export function createZeroAntennas(): ControllerAntennas {
  return [0, 0]
}

export function createZeroControllerValues(): ControllerValues {
  return {
    headPose: createZeroHeadPose(),
    bodyYaw: 0,
    antennas: createZeroAntennas(),
  }
}
