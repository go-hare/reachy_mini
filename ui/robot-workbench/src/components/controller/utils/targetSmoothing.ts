import {
  createZeroAntennas,
  createZeroControllerValues,
  createZeroHeadPose,
  type ControllerValues,
} from "@/components/controller/types"
import { smoothValue } from "@/components/controller/utils/inputSmoothing"

const SMOOTHING_FACTORS = {
  POSITION: 0.02,
  ROTATION: 0.02,
  BODY_YAW: 0.0375,
  ANTENNA: 0.03,
} as const

function cloneValues(values: ControllerValues): ControllerValues {
  return {
    headPose: { ...values.headPose },
    bodyYaw: values.bodyYaw,
    antennas: [...values.antennas] as [number, number],
  }
}

export class TargetSmoothingManager {
  private currentValues: ControllerValues = createZeroControllerValues()
  private targetValues: ControllerValues = createZeroControllerValues()

  setTargets(targets: Partial<ControllerValues>) {
    if (targets.headPose) {
      this.targetValues.headPose = { ...this.targetValues.headPose, ...targets.headPose }
    }

    if (typeof targets.bodyYaw === "number") {
      this.targetValues.bodyYaw = targets.bodyYaw
    }

    if (targets.antennas) {
      this.targetValues.antennas = [...targets.antennas] as [number, number]
    }
  }

  update(): ControllerValues {
    this.currentValues.headPose = {
      x: smoothValue(
        this.currentValues.headPose.x,
        this.targetValues.headPose.x,
        SMOOTHING_FACTORS.POSITION
      ),
      y: smoothValue(
        this.currentValues.headPose.y,
        this.targetValues.headPose.y,
        SMOOTHING_FACTORS.POSITION
      ),
      z: smoothValue(
        this.currentValues.headPose.z,
        this.targetValues.headPose.z,
        SMOOTHING_FACTORS.POSITION
      ),
      pitch: smoothValue(
        this.currentValues.headPose.pitch,
        this.targetValues.headPose.pitch,
        SMOOTHING_FACTORS.ROTATION
      ),
      yaw: smoothValue(
        this.currentValues.headPose.yaw,
        this.targetValues.headPose.yaw,
        SMOOTHING_FACTORS.ROTATION
      ),
      roll: smoothValue(
        this.currentValues.headPose.roll,
        this.targetValues.headPose.roll,
        SMOOTHING_FACTORS.ROTATION
      ),
    }

    this.currentValues.bodyYaw = smoothValue(
      this.currentValues.bodyYaw,
      this.targetValues.bodyYaw,
      SMOOTHING_FACTORS.BODY_YAW
    )

    this.currentValues.antennas = [
      smoothValue(
        this.currentValues.antennas[0],
        this.targetValues.antennas[0],
        SMOOTHING_FACTORS.ANTENNA
      ),
      smoothValue(
        this.currentValues.antennas[1],
        this.targetValues.antennas[1],
        SMOOTHING_FACTORS.ANTENNA
      ),
    ]

    return cloneValues(this.currentValues)
  }

  getCurrentValues() {
    return cloneValues(this.currentValues)
  }

  getTargetValues() {
    return cloneValues(this.targetValues)
  }

  reset() {
    this.currentValues = {
      headPose: createZeroHeadPose(),
      bodyYaw: 0,
      antennas: createZeroAntennas(),
    }
    this.targetValues = {
      headPose: createZeroHeadPose(),
      bodyYaw: 0,
      antennas: createZeroAntennas(),
    }
  }

  sync(values: Partial<ControllerValues>) {
    if (values.headPose) {
      this.currentValues.headPose = { ...values.headPose }
      this.targetValues.headPose = { ...values.headPose }
    }

    if (typeof values.bodyYaw === "number") {
      this.currentValues.bodyYaw = values.bodyYaw
      this.targetValues.bodyYaw = values.bodyYaw
    }

    if (values.antennas) {
      this.currentValues.antennas = [...values.antennas] as [number, number]
      this.targetValues.antennas = [...values.antennas] as [number, number]
    }
  }
}
