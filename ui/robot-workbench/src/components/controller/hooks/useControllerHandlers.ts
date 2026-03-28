import { useCallback, useRef } from "react"
import { useController } from "@/components/controller/context/ControllerContext"
import {
  createZeroAntennas,
  createZeroHeadPose,
  type ControllerAntennas,
  type ControllerHeadPose,
} from "@/components/controller/types"
import { EXTENDED_ROBOT_RANGES, ROBOT_POSITION_RANGES } from "@/components/controller/utils/inputConstants"
import { clamp } from "@/components/controller/utils/inputHelpers"

const BODY_YAW_RANGE = { min: (-160 * Math.PI) / 180, max: (160 * Math.PI) / 180 }

type SendCommandFn = (
  headPose: ControllerHeadPose,
  antennas: ControllerAntennas,
  bodyYaw: number
) => Promise<unknown> | void

export function useControllerHandlers({ sendCommand }: { sendCommand: SendCommandFn }) {
  const { state, actions, smoother, isActive } = useController()
  const dragStartRef = useRef<unknown>(null)

  const flushFinalCommand = useCallback(() => {
    requestAnimationFrame(() => {
      const smoothed = smoother.getCurrentValues()
      void sendCommand(smoothed.headPose, smoothed.antennas, smoothed.bodyYaw)
    })
  }, [sendCommand, smoother])

  const handleHeadPoseChange = useCallback(
    (updates: Partial<ControllerHeadPose>, continuous = false) => {
      if (!isActive) return

      const nextHeadPose = { ...state.headPose, ...updates }
      const clampedHeadPose = {
        x: clamp(
          nextHeadPose.x,
          EXTENDED_ROBOT_RANGES.POSITION.min,
          EXTENDED_ROBOT_RANGES.POSITION.max
        ),
        y: clamp(
          nextHeadPose.y,
          EXTENDED_ROBOT_RANGES.POSITION.min,
          EXTENDED_ROBOT_RANGES.POSITION.max
        ),
        z: clamp(nextHeadPose.z, ROBOT_POSITION_RANGES.POSITION.min, ROBOT_POSITION_RANGES.POSITION.max),
        pitch: clamp(nextHeadPose.pitch, EXTENDED_ROBOT_RANGES.PITCH.min, EXTENDED_ROBOT_RANGES.PITCH.max),
        yaw: clamp(nextHeadPose.yaw, EXTENDED_ROBOT_RANGES.YAW.min, EXTENDED_ROBOT_RANGES.YAW.max),
        roll: clamp(nextHeadPose.roll, ROBOT_POSITION_RANGES.ROLL.min, ROBOT_POSITION_RANGES.ROLL.max),
      }

      actions.updateHeadPose(clampedHeadPose)
      smoother.setTargets({ headPose: clampedHeadPose })

      if (continuous) {
        if (!dragStartRef.current) {
          dragStartRef.current = { headPose: { ...state.headPose }, bodyYaw: state.bodyYaw }
          actions.startMouseDrag()
        }
        return
      }

      dragStartRef.current = null
      actions.endInteraction()
      flushFinalCommand()
    },
    [actions, flushFinalCommand, isActive, smoother, state.bodyYaw, state.headPose]
  )

  const handleBodyYawChange = useCallback(
    (value: number, continuous = false) => {
      if (!isActive) return

      const clampedValue = clamp(
        typeof value === "number" && Number.isFinite(value) ? value : 0,
        BODY_YAW_RANGE.min,
        BODY_YAW_RANGE.max
      )

      actions.updateBodyYaw(clampedValue)
      smoother.setTargets({ bodyYaw: clampedValue })

      if (continuous) {
        if (!dragStartRef.current) {
          dragStartRef.current = { bodyYaw: state.bodyYaw }
          actions.startMouseDrag()
        }
        return
      }

      dragStartRef.current = null
      actions.endInteraction()
      flushFinalCommand()
    },
    [actions, flushFinalCommand, isActive, smoother, state.bodyYaw]
  )

  const handleAntennasChange = useCallback(
    (antenna: "left" | "right", value: number, continuous = false) => {
      if (!isActive) return

      const currentAntennas = state.antennas ?? createZeroAntennas()
      const nextAntennas =
        antenna === "left"
          ? [value, currentAntennas[1]]
          : [currentAntennas[0], value]

      const clampedAntennas: ControllerAntennas = [
        clamp(nextAntennas[0], ROBOT_POSITION_RANGES.ANTENNA.min, ROBOT_POSITION_RANGES.ANTENNA.max),
        clamp(nextAntennas[1], ROBOT_POSITION_RANGES.ANTENNA.min, ROBOT_POSITION_RANGES.ANTENNA.max),
      ]

      actions.updateAntennas(clampedAntennas)
      smoother.setTargets({ antennas: clampedAntennas })

      if (continuous) {
        if (!dragStartRef.current) {
          dragStartRef.current = { antennas: [...currentAntennas] }
          actions.startMouseDrag()
        }
        return
      }

      dragStartRef.current = null
      actions.endInteraction()
      flushFinalCommand()
    },
    [actions, flushFinalCommand, isActive, smoother, state.antennas]
  )

  const handleDragEnd = useCallback(() => {
    dragStartRef.current = null
    actions.endInteraction()
  }, [actions])

  const resetAllValues = useCallback(() => {
    const zeroHeadPose = createZeroHeadPose()
    const zeroAntennas = createZeroAntennas()

    actions.startReset()
    actions.updateAll({
      headPose: zeroHeadPose,
      bodyYaw: 0,
      antennas: zeroAntennas,
    })
    smoother.setTargets({
      headPose: zeroHeadPose,
      bodyYaw: 0,
      antennas: zeroAntennas,
    })

    requestAnimationFrame(() => {
      actions.resetToZero()
      void sendCommand(zeroHeadPose, zeroAntennas, 0)
    })
  }, [actions, sendCommand, smoother])

  return {
    localValues: {
      headPose: state.headPose,
      bodyYaw: state.bodyYaw,
      antennas: state.antennas,
    },
    getSmoothedValues: () => smoother.getCurrentValues(),
    handleChange: handleHeadPoseChange,
    handleBodyYawChange,
    handleAntennasChange,
    handleDragEnd,
    resetAllValues,
  }
}
