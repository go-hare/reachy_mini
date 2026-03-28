import { useEffect, useRef, useState } from "react"
import { useController } from "@/components/controller/context/ControllerContext"
import {
  createZeroControllerValues,
  type ControllerHeadPose,
  type ControllerValues,
} from "@/components/controller/types"
import { ROBOT_POSITION_RANGES } from "@/components/controller/utils/inputConstants"
import { clamp } from "@/components/controller/utils/inputHelpers"
import { mapRobotToAPI } from "@/components/controller/utils/inputMappings"

const UI_UPDATE_INTERVAL_MS = 1000 / 15

export function useControllerSmoothing({
  sendCommand,
}: {
  sendCommand: (headPose: ControllerHeadPose, antennas: [number, number], bodyYaw: number) => void
}) {
  const { smoother, isDragging, isActive } = useController()
  const rafRef = useRef<number | null>(null)
  const lastUIUpdateRef = useRef(0)
  const [smoothedValues, setSmoothedValues] = useState<ControllerValues>(createZeroControllerValues())

  useEffect(() => {
    if (!isActive) return

    const smoothingLoop = () => {
      const currentSmoothed = smoother.update()
      const targetValues = smoother.getTargetValues()
      const hasReachedTarget = isAtTarget(currentSmoothed, targetValues, 0.01)

      if (isDragging || !hasReachedTarget) {
        const apiHeadPose = transformForAPI(currentSmoothed.headPose)
        sendCommand(apiHeadPose, currentSmoothed.antennas, currentSmoothed.bodyYaw)
      }

      const now = performance.now()
      if (now - lastUIUpdateRef.current >= UI_UPDATE_INTERVAL_MS) {
        lastUIUpdateRef.current = now
        setSmoothedValues({
          headPose: { ...currentSmoothed.headPose },
          bodyYaw: currentSmoothed.bodyYaw,
          antennas: [...currentSmoothed.antennas] as [number, number],
        })
      }

      rafRef.current = requestAnimationFrame(smoothingLoop)
    }

    rafRef.current = requestAnimationFrame(smoothingLoop)

    return () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [isActive, isDragging, sendCommand, smoother])

  return { smoothedValues }
}

function isAtTarget(current: ControllerValues, target: ControllerValues, tolerance: number) {
  const headPoseDiff =
    Math.abs(current.headPose.x - target.headPose.x) +
    Math.abs(current.headPose.y - target.headPose.y) +
    Math.abs(current.headPose.z - target.headPose.z) +
    Math.abs(current.headPose.pitch - target.headPose.pitch) +
    Math.abs(current.headPose.yaw - target.headPose.yaw) +
    Math.abs(current.headPose.roll - target.headPose.roll)

  const bodyYawDiff = Math.abs(current.bodyYaw - target.bodyYaw)
  const antennasDiff =
    Math.abs(current.antennas[0] - target.antennas[0]) +
    Math.abs(current.antennas[1] - target.antennas[1])

  return headPoseDiff < tolerance && bodyYawDiff < tolerance && antennasDiff < tolerance
}

function transformForAPI(headPose: ControllerHeadPose): ControllerHeadPose {
  return {
    x: clamp(
      mapRobotToAPI(headPose.x, "positionX"),
      ROBOT_POSITION_RANGES.POSITION.min,
      ROBOT_POSITION_RANGES.POSITION.max
    ),
    y: clamp(
      mapRobotToAPI(headPose.y, "positionY"),
      ROBOT_POSITION_RANGES.POSITION.min,
      ROBOT_POSITION_RANGES.POSITION.max
    ),
    z: clamp(headPose.z, ROBOT_POSITION_RANGES.POSITION.min, ROBOT_POSITION_RANGES.POSITION.max),
    pitch: clamp(
      mapRobotToAPI(headPose.pitch, "pitch"),
      ROBOT_POSITION_RANGES.PITCH.min,
      ROBOT_POSITION_RANGES.PITCH.max
    ),
    yaw: clamp(
      mapRobotToAPI(headPose.yaw, "yaw"),
      ROBOT_POSITION_RANGES.YAW.min,
      ROBOT_POSITION_RANGES.YAW.max
    ),
    roll: clamp(
      mapRobotToAPI(headPose.roll, "roll"),
      ROBOT_POSITION_RANGES.ROLL.min,
      ROBOT_POSITION_RANGES.ROLL.max
    ),
  }
}
