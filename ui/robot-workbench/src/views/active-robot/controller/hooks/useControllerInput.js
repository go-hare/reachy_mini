import { useEffect, useRef, useCallback } from 'react';
import { useController } from '../context/ControllerContext';
import { getInputManager } from '@utils/InputManager';
import {
  ROBOT_POSITION_RANGES,
  EXTENDED_ROBOT_RANGES,
  INPUT_SMOOTHING_FACTORS,
  INPUT_MAPPING_FACTORS,
  INPUT_THRESHOLDS,
  TIMING,
} from '@utils/inputConstants';
import { hasActiveInput, clamp } from '@utils/inputHelpers';
import { smoothInputs, getDeltaTime } from '@utils/inputSmoothing';
import { mapInputToRobot } from '@utils/inputMappings';

// Body yaw range
const BODY_YAW_RANGE = { min: (-160 * Math.PI) / 180, max: (160 * Math.PI) / 180 };

/**
 * Hook for gamepad/keyboard input processing
 * Integrates with the new ControllerContext architecture
 */
export function useControllerInput() {
  const { state, actions, smoother, isDragging, isActive } = useController();

  // Smoothing state for inputs (middleware layer)
  const smoothedInputsRef = useRef({
    moveForward: 0,
    moveRight: 0,
    moveUp: 0,
    lookHorizontal: 0,
    lookVertical: 0,
    roll: 0,
    bodyYaw: 0,
    antennaLeft: 0,
    antennaRight: 0,
  });

  const lastFrameTimeRef = useRef(performance.now());
  const wasActiveRef = useRef(false);

  /**
   * Process raw inputs from gamepad/keyboard
   */
  const processInputs = useCallback(
    rawInputs => {
      if (!isActive) return;

      // Calculate delta time for frame-rate independent smoothing
      const { currentTime } = getDeltaTime(lastFrameTimeRef.current);
      lastFrameTimeRef.current = currentTime;

      // Apply exponential smoothing to inputs
      smoothedInputsRef.current = smoothInputs(smoothedInputsRef.current, rawInputs, {
        moveForward: INPUT_SMOOTHING_FACTORS.POSITION,
        moveRight: INPUT_SMOOTHING_FACTORS.POSITION,
        moveUp: INPUT_SMOOTHING_FACTORS.POSITION_Z,
        lookHorizontal: INPUT_SMOOTHING_FACTORS.ROTATION,
        lookVertical: INPUT_SMOOTHING_FACTORS.ROTATION,
        roll: INPUT_SMOOTHING_FACTORS.POSITION,
        bodyYaw: INPUT_SMOOTHING_FACTORS.BODY_YAW,
        antennaLeft: INPUT_SMOOTHING_FACTORS.ANTENNA,
        antennaRight: INPUT_SMOOTHING_FACTORS.ANTENNA,
      });

      const inputs = smoothedInputsRef.current;

      // Check if any input is active
      const hasInput = hasActiveInput(inputs, INPUT_THRESHOLDS.ACTIVE_INPUT);

      if (!hasInput) {
        // No active input - end gamepad interaction if it was active
        if (wasActiveRef.current) {
          wasActiveRef.current = false;
          actions.endInteraction();
        }
        return;
      }

      // Input is active
      if (!wasActiveRef.current) {
        wasActiveRef.current = true;
        actions.startGamepadInput();
      }

      // Get current values from state
      const currentHeadPose = state.headPose;
      const currentBodyYaw = state.bodyYaw;

      // Calculate new positions
      const POSITION_SENSITIVITY = INPUT_MAPPING_FACTORS.POSITION;
      const ROTATION_SENSITIVITY = INPUT_MAPPING_FACTORS.ROTATION;
      const BODY_YAW_SENSITIVITY = INPUT_MAPPING_FACTORS.BODY_YAW;

      // Position X/Y (absolute mapping from stick position)
      const newX = inputs.moveForward * EXTENDED_ROBOT_RANGES.POSITION.max * POSITION_SENSITIVITY;
      const newY = inputs.moveRight * EXTENDED_ROBOT_RANGES.POSITION.max * POSITION_SENSITIVITY;

      // Position Z (incremental)
      const zIncrement = inputs.moveUp * ROBOT_POSITION_RANGES.POSITION.max * POSITION_SENSITIVITY;
      const newZ = currentHeadPose.z + zIncrement;

      // Rotation (with mapping)
      const mappedPitch = mapInputToRobot(inputs.lookVertical, 'pitch');
      const mappedYaw = mapInputToRobot(inputs.lookHorizontal, 'yaw');
      const newPitch = mappedPitch * EXTENDED_ROBOT_RANGES.PITCH.max * ROTATION_SENSITIVITY;
      const newYaw = mappedYaw * EXTENDED_ROBOT_RANGES.YAW.max * ROTATION_SENSITIVITY;
      const newRoll = inputs.roll * ROBOT_POSITION_RANGES.ROLL.max * ROTATION_SENSITIVITY;

      // Body yaw (incremental)
      const bodyYawRange = BODY_YAW_RANGE.max - BODY_YAW_RANGE.min;
      const bodyYawIncrement = inputs.bodyYaw * bodyYawRange * BODY_YAW_SENSITIVITY;
      const newBodyYaw = clamp(
        currentBodyYaw + bodyYawIncrement,
        BODY_YAW_RANGE.min,
        BODY_YAW_RANGE.max
      );

      // Antennas (absolute mapping)
      const antennaRange = ROBOT_POSITION_RANGES.ANTENNA.max - ROBOT_POSITION_RANGES.ANTENNA.min;
      const newAntennaLeft = clamp(
        ROBOT_POSITION_RANGES.ANTENNA.min + inputs.antennaLeft * antennaRange,
        ROBOT_POSITION_RANGES.ANTENNA.min,
        ROBOT_POSITION_RANGES.ANTENNA.max
      );
      const newAntennaRight = clamp(
        ROBOT_POSITION_RANGES.ANTENNA.min + inputs.antennaRight * antennaRange,
        ROBOT_POSITION_RANGES.ANTENNA.min,
        ROBOT_POSITION_RANGES.ANTENNA.max
      );

      // Clamp all values
      const targetHeadPose = {
        x: clamp(newX, EXTENDED_ROBOT_RANGES.POSITION.min, EXTENDED_ROBOT_RANGES.POSITION.max),
        y: clamp(newY, EXTENDED_ROBOT_RANGES.POSITION.min, EXTENDED_ROBOT_RANGES.POSITION.max),
        z: clamp(newZ, ROBOT_POSITION_RANGES.POSITION.min, ROBOT_POSITION_RANGES.POSITION.max),
        pitch: clamp(newPitch, EXTENDED_ROBOT_RANGES.PITCH.min, EXTENDED_ROBOT_RANGES.PITCH.max),
        yaw: clamp(newYaw, EXTENDED_ROBOT_RANGES.YAW.min, EXTENDED_ROBOT_RANGES.YAW.max),
        roll: clamp(newRoll, ROBOT_POSITION_RANGES.ROLL.min, ROBOT_POSITION_RANGES.ROLL.max),
      };

      const targetAntennas = [newAntennaLeft, newAntennaRight];

      // Update context state
      actions.updateAll({
        headPose: targetHeadPose,
        bodyYaw: newBodyYaw,
        antennas: targetAntennas,
      });

      // Update smoothing targets
      smoother.setTargets({
        headPose: targetHeadPose,
        antennas: targetAntennas,
        bodyYaw: newBodyYaw,
      });
    },
    [isActive, state.headPose, state.bodyYaw, actions, smoother]
  );

  // Subscribe to input manager
  useEffect(() => {
    if (!isActive) return;

    const inputManager = getInputManager();
    const unsubscribe = inputManager.addListener(processInputs);

    return () => {
      unsubscribe();
      // Reset on cleanup
      if (wasActiveRef.current) {
        wasActiveRef.current = false;
        actions.endInteraction();
      }
    };
  }, [isActive, processInputs, actions]);

  return { processInputs };
}
