export const ROBOT_POSITION_RANGES = {
  POSITION: { min: -0.05, max: 0.05 },
  PITCH: { min: -0.8, max: 0.8 },
  YAW: { min: -1.2, max: 1.2 },
  ROLL: { min: -0.5, max: 0.5 },
  ANTENNA: { min: (-160 * Math.PI) / 180, max: (160 * Math.PI) / 180 },
} as const

export const EXTENDED_ROBOT_RANGES = {
  POSITION: { min: -0.15, max: 0.15 },
  PITCH: { min: -2.4, max: 2.4 },
  YAW: { min: -3.6, max: 3.6 },
} as const
