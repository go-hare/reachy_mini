const ROBOT_TO_DISPLAY_MAPPINGS = {
  positionX: (value: number) => -value,
  positionY: (value: number) => value,
  pitch: (value: number) => -value,
  yaw: (value: number) => -value,
} as const

const ROBOT_TO_API_MAPPINGS = {
  positionX: (value: number) => -value,
  positionY: (value: number) => value,
  pitch: (value: number) => -value,
  yaw: (value: number) => -value,
  roll: (value: number) => value,
} as const

type DisplayComponent = keyof typeof ROBOT_TO_DISPLAY_MAPPINGS
type ApiComponent = keyof typeof ROBOT_TO_API_MAPPINGS

export function mapRobotToDisplay(value: number, component: DisplayComponent) {
  return ROBOT_TO_DISPLAY_MAPPINGS[component](value)
}

export function mapDisplayToRobot(value: number, component: DisplayComponent) {
  return ROBOT_TO_DISPLAY_MAPPINGS[component](value)
}

export function mapRobotToAPI(value: number, component: ApiComponent) {
  return ROBOT_TO_API_MAPPINGS[component](value)
}
