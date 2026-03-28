import { useEffect } from "react"
import { useController } from "@/components/controller/context/ControllerContext"
import { ControllerMode, type ControllerAntennas, type ControllerHeadPose } from "@/components/controller/types"
import { type ReachyFullState } from "@/lib/reachy-daemon"

const SYNC_TOLERANCE = 0.01
const MAJOR_CHANGE_TOLERANCE = 0.1

export function useControllerSync({
  snapshot,
  enabled = true,
}: {
  snapshot: ReachyFullState | null
  enabled?: boolean
}) {
  const { state, actions, smoother, isDragging, isActive } = useController()

  useEffect(() => {
    if (!enabled || !isActive || !snapshot) return

    const headPose = asHeadPose(snapshot.head_pose)
    if (!headPose) return

    const antennas: ControllerAntennas = Array.isArray(snapshot.antennas_position)
      ? [snapshot.antennas_position[0] ?? 0, snapshot.antennas_position[1] ?? 0]
      : [0, 0]

    const robotValues = {
      headPose,
      bodyYaw: typeof snapshot.body_yaw === "number" ? snapshot.body_yaw : 0,
      antennas,
    }

    if (isDragging) return
    if (state.mode !== ControllerMode.IDLE) return

    const timeSinceInteraction = Date.now() - state.lastInteractionTime
    if (timeSinceInteraction < 30_000) return

    const hasMajorChange = checkMajorChange(state, robotValues)
    if (!hasMajorChange) return

    const targetValues = smoother.getTargetValues()
    const isCloseToTarget = isCloseEnough(robotValues, targetValues, SYNC_TOLERANCE)
    if (isCloseToTarget) return

    actions.syncFromRobot(robotValues)
    smoother.sync(robotValues)
  }, [actions, enabled, isActive, isDragging, snapshot, smoother, state])
}

function asHeadPose(value: ReachyFullState["head_pose"]): ControllerHeadPose | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null

  const maybePose = value as Partial<ControllerHeadPose>
  if (
    typeof maybePose.x === "number" &&
    typeof maybePose.y === "number" &&
    typeof maybePose.z === "number" &&
    typeof maybePose.pitch === "number" &&
    typeof maybePose.yaw === "number" &&
    typeof maybePose.roll === "number"
  ) {
    return {
      x: maybePose.x,
      y: maybePose.y,
      z: maybePose.z,
      pitch: maybePose.pitch,
      yaw: maybePose.yaw,
      roll: maybePose.roll,
    }
  }

  return null
}

function checkMajorChange(
  state: ReturnType<typeof useController>["state"],
  robotValues: {
    headPose: ControllerHeadPose
    bodyYaw: number
    antennas: ControllerAntennas
  }
) {
  const headDiff =
    Math.abs(state.headPose.x - robotValues.headPose.x) +
    Math.abs(state.headPose.y - robotValues.headPose.y) +
    Math.abs(state.headPose.z - robotValues.headPose.z) +
    Math.abs(state.headPose.pitch - robotValues.headPose.pitch) +
    Math.abs(state.headPose.yaw - robotValues.headPose.yaw) +
    Math.abs(state.headPose.roll - robotValues.headPose.roll)

  const bodyYawDiff = Math.abs(state.bodyYaw - robotValues.bodyYaw)
  const antennasDiff =
    Math.abs(state.antennas[0] - robotValues.antennas[0]) +
    Math.abs(state.antennas[1] - robotValues.antennas[1])

  return (
    headDiff > MAJOR_CHANGE_TOLERANCE ||
    bodyYawDiff > MAJOR_CHANGE_TOLERANCE ||
    antennasDiff > MAJOR_CHANGE_TOLERANCE
  )
}

function isCloseEnough(
  values1: {
    headPose: ControllerHeadPose
    bodyYaw: number
    antennas: ControllerAntennas
  },
  values2: {
    headPose: ControllerHeadPose
    bodyYaw: number
    antennas: ControllerAntennas
  },
  tolerance: number
) {
  const headClose =
    Math.abs(values1.headPose.x - values2.headPose.x) < tolerance &&
    Math.abs(values1.headPose.y - values2.headPose.y) < tolerance &&
    Math.abs(values1.headPose.z - values2.headPose.z) < tolerance &&
    Math.abs(values1.headPose.pitch - values2.headPose.pitch) < tolerance &&
    Math.abs(values1.headPose.yaw - values2.headPose.yaw) < tolerance &&
    Math.abs(values1.headPose.roll - values2.headPose.roll) < tolerance

  const bodyYawClose = Math.abs(values1.bodyYaw - values2.bodyYaw) < tolerance
  const antennasClose =
    Math.abs(values1.antennas[0] - values2.antennas[0]) < tolerance &&
    Math.abs(values1.antennas[1] - values2.antennas[1]) < tolerance

  return headClose && bodyYawClose && antennasClose
}
