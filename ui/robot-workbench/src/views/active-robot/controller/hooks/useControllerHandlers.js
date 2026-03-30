import { useCallback, useRef } from 'react';
import { useController } from '../context/ControllerContext';
import { ROBOT_POSITION_RANGES, EXTENDED_ROBOT_RANGES } from '@utils/inputConstants';
import { clamp } from '@utils/inputHelpers';

// Body yaw range constants
const BODY_YAW_RANGE = { min: (-160 * Math.PI) / 180, max: (160 * Math.PI) / 180 };

/**
 * Unified controller handlers hook
 * Replaces the duplicated handler logic with a clean, DRY implementation
 */
export function useControllerHandlers({ sendCommand }) {
  const { state, actions, smoother, isActive } = useController();

  // Refs for logging (non-reactive)
  const lastLogTimeRef = useRef(0);
  const dragStartRef = useRef(null);

  /**
   * Generic value update handler
   * Handles both continuous (dragging) and discrete (release) updates
   */
  const createValueHandler = useCallback(
    (updateFn, smoothingKey) => {
      return (value, continuous = false) => {
        if (!isActive) return;

        // Update local state
        updateFn(value);

        // Update smoothing targets
        smoother.setTargets({ [smoothingKey]: value });

        if (continuous) {
          // Start drag if not already
          if (!dragStartRef.current) {
            dragStartRef.current = { ...state };
            actions.startMouseDrag();
          }
        } else {
          // End interaction and send final command
          if (dragStartRef.current) {
            dragStartRef.current = null;
          }
          actions.endInteraction();

          // Send final position via RAF (replaces setTimeout)
          requestAnimationFrame(() => {
            const smoothed = smoother.getCurrentValues();
            sendCommand(smoothed.headPose, smoothed.antennas, smoothed.bodyYaw);
          });
        }
      };
    },
    [state, actions, smoother, sendCommand, isActive]
  );

  /**
   * Handle head pose changes (position + orientation)
   */
  const handleHeadPoseChange = useCallback(
    (updates, continuous = false) => {
      if (!isActive) return;

      const newHeadPose = { ...state.headPose, ...updates };

      // Clamp all values
      const clampedHeadPose = {
        x: clamp(
          newHeadPose.x,
          EXTENDED_ROBOT_RANGES.POSITION.min,
          EXTENDED_ROBOT_RANGES.POSITION.max
        ),
        y: clamp(
          newHeadPose.y,
          EXTENDED_ROBOT_RANGES.POSITION.min,
          EXTENDED_ROBOT_RANGES.POSITION.max
        ),
        z: clamp(
          newHeadPose.z,
          ROBOT_POSITION_RANGES.POSITION.min,
          ROBOT_POSITION_RANGES.POSITION.max
        ),
        pitch: clamp(
          newHeadPose.pitch,
          EXTENDED_ROBOT_RANGES.PITCH.min,
          EXTENDED_ROBOT_RANGES.PITCH.max
        ),
        yaw: clamp(newHeadPose.yaw, EXTENDED_ROBOT_RANGES.YAW.min, EXTENDED_ROBOT_RANGES.YAW.max),
        roll: clamp(
          newHeadPose.roll,
          ROBOT_POSITION_RANGES.ROLL.min,
          ROBOT_POSITION_RANGES.ROLL.max
        ),
      };

      // Update state and smoothing
      actions.updateHeadPose(clampedHeadPose);
      smoother.setTargets({ headPose: clampedHeadPose });

      if (continuous) {
        if (!dragStartRef.current) {
          dragStartRef.current = { headPose: { ...state.headPose }, bodyYaw: state.bodyYaw };
          actions.startMouseDrag();
        }
      } else {
        dragStartRef.current = null;
        actions.endInteraction();

        requestAnimationFrame(() => {
          const smoothed = smoother.getCurrentValues();
          sendCommand(smoothed.headPose, smoothed.antennas, smoothed.bodyYaw);
        });
      }
    },
    [state.headPose, state.bodyYaw, actions, smoother, sendCommand, isActive]
  );

  /**
   * Handle body yaw changes
   */
  const handleBodyYawChange = useCallback(
    (value, continuous = false) => {
      if (!isActive) return;

      const clampedValue = clamp(
        typeof value === 'number' && !isNaN(value) ? value : 0,
        BODY_YAW_RANGE.min,
        BODY_YAW_RANGE.max
      );

      actions.updateBodyYaw(clampedValue);
      smoother.setTargets({ bodyYaw: clampedValue });

      if (continuous) {
        if (!dragStartRef.current) {
          dragStartRef.current = { bodyYaw: state.bodyYaw };
          actions.startMouseDrag();
        }
      } else {
        dragStartRef.current = null;
        actions.endInteraction();

        requestAnimationFrame(() => {
          const smoothed = smoother.getCurrentValues();
          sendCommand(smoothed.headPose, smoothed.antennas, smoothed.bodyYaw);
        });
      }
    },
    [state.bodyYaw, actions, smoother, sendCommand, isActive]
  );

  /**
   * Handle antenna changes
   */
  const handleAntennasChange = useCallback(
    (antenna, value, continuous = false) => {
      if (!isActive) return;

      const currentAntennas = state.antennas || [0, 0];
      const newAntennas =
        antenna === 'left' ? [value, currentAntennas[1]] : [currentAntennas[0], value];

      const clampedAntennas = [
        clamp(newAntennas[0], ROBOT_POSITION_RANGES.ANTENNA.min, ROBOT_POSITION_RANGES.ANTENNA.max),
        clamp(newAntennas[1], ROBOT_POSITION_RANGES.ANTENNA.min, ROBOT_POSITION_RANGES.ANTENNA.max),
      ];

      actions.updateAntennas(clampedAntennas);
      smoother.setTargets({ antennas: clampedAntennas });

      if (continuous) {
        if (!dragStartRef.current) {
          dragStartRef.current = { antennas: [...currentAntennas] };
          actions.startMouseDrag();
        }
      } else {
        dragStartRef.current = null;
        actions.endInteraction();

        requestAnimationFrame(() => {
          const smoothed = smoother.getCurrentValues();
          sendCommand(smoothed.headPose, smoothed.antennas, smoothed.bodyYaw);
        });
      }
    },
    [state.antennas, actions, smoother, sendCommand, isActive]
  );

  /**
   * Handle drag end (for joysticks)
   */
  const handleDragEnd = useCallback(() => {
    dragStartRef.current = null;
    actions.endInteraction();
  }, [actions]);

  /**
   * Reset all values to zero with smooth animation
   */
  const resetAllValues = useCallback(() => {
    actions.startReset();
    smoother.setTargets({
      headPose: { x: 0, y: 0, z: 0, pitch: 0, yaw: 0, roll: 0 },
      bodyYaw: 0,
      antennas: [0, 0],
    });
  }, [actions, smoother]);

  return {
    // Values (from context state)
    localValues: {
      headPose: state.headPose,
      bodyYaw: state.bodyYaw,
      antennas: state.antennas,
    },

    // Smoothed values (from smoother)
    getSmoothedValues: () => smoother.getCurrentValues(),

    // Handlers
    handleChange: handleHeadPoseChange,
    handleBodyYawChange,
    handleAntennasChange,
    handleDragEnd,
    resetAllValues,
  };
}
