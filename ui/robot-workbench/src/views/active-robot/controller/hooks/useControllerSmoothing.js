import { useEffect, useRef, useState } from 'react';
import { useController } from '../context/ControllerContext';
import { ROBOT_POSITION_RANGES } from '@utils/inputConstants';
import { clamp } from '@utils/inputHelpers';
import { mapRobotToAPI } from '@utils/inputMappings';

// UI updates throttled to 15fps for performance
const UI_UPDATE_INTERVAL_MS = 1000 / 15;

/**
 * Smoothing loop hook
 * Runs the continuous smoothing animation and sends commands to robot
 */
export function useControllerSmoothing({ sendCommand }) {
  const { smoother, isDragging, isActive } = useController();

  const rafRef = useRef(null);
  const lastUIUpdateRef = useRef(0);

  // Smoothed values for UI (ghost position)
  const [smoothedValues, setSmoothedValues] = useState({
    headPose: { x: 0, y: 0, z: 0, pitch: 0, yaw: 0, roll: 0 },
    bodyYaw: 0,
    antennas: [0, 0],
  });

  useEffect(() => {
    if (!isActive) return;

    const smoothingLoop = () => {
      // Update smoothed values towards targets
      const currentSmoothed = smoother.update();
      const targetValues = smoother.getTargetValues();

      // Check if ghost has reached destination
      const hasReachedTarget = isAtTarget(currentSmoothed, targetValues, 0.01);

      // Send commands if dragging OR ghost hasn't reached target
      if (isDragging || !hasReachedTarget) {
        const apiHeadPose = transformForAPI(currentSmoothed.headPose);
        sendCommand(apiHeadPose, currentSmoothed.antennas, currentSmoothed.bodyYaw);
      }

      // Throttle UI updates to 15fps
      const now = performance.now();
      if (now - lastUIUpdateRef.current >= UI_UPDATE_INTERVAL_MS) {
        lastUIUpdateRef.current = now;
        setSmoothedValues({
          headPose: { ...currentSmoothed.headPose },
          bodyYaw: currentSmoothed.bodyYaw,
          antennas: [...currentSmoothed.antennas],
        });
      }

      rafRef.current = requestAnimationFrame(smoothingLoop);
    };

    rafRef.current = requestAnimationFrame(smoothingLoop);

    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [isActive, isDragging, smoother, sendCommand]);

  return { smoothedValues };
}

/**
 * Check if current values are at target
 */
function isAtTarget(current, target, tolerance) {
  const headPoseDiff =
    Math.abs(current.headPose.x - target.headPose.x) +
    Math.abs(current.headPose.y - target.headPose.y) +
    Math.abs(current.headPose.z - target.headPose.z) +
    Math.abs(current.headPose.pitch - target.headPose.pitch) +
    Math.abs(current.headPose.yaw - target.headPose.yaw) +
    Math.abs(current.headPose.roll - target.headPose.roll);

  const bodyYawDiff = Math.abs(current.bodyYaw - target.bodyYaw);
  const antennasDiff =
    Math.abs(current.antennas[0] - target.antennas[0]) +
    Math.abs(current.antennas[1] - target.antennas[1]);

  return headPoseDiff < tolerance && bodyYawDiff < tolerance && antennasDiff < tolerance;
}

/**
 * Transform head pose for API (apply inversions and clamp)
 */
function transformForAPI(headPose) {
  return {
    x: clamp(
      mapRobotToAPI(headPose.x, 'positionX'),
      ROBOT_POSITION_RANGES.POSITION.min,
      ROBOT_POSITION_RANGES.POSITION.max
    ),
    y: clamp(
      mapRobotToAPI(headPose.y, 'positionY'),
      ROBOT_POSITION_RANGES.POSITION.min,
      ROBOT_POSITION_RANGES.POSITION.max
    ),
    z: clamp(headPose.z, ROBOT_POSITION_RANGES.POSITION.min, ROBOT_POSITION_RANGES.POSITION.max),
    pitch: clamp(
      mapRobotToAPI(headPose.pitch, 'pitch'),
      ROBOT_POSITION_RANGES.PITCH.min,
      ROBOT_POSITION_RANGES.PITCH.max
    ),
    yaw: clamp(
      mapRobotToAPI(headPose.yaw, 'yaw'),
      ROBOT_POSITION_RANGES.YAW.min,
      ROBOT_POSITION_RANGES.YAW.max
    ),
    roll: clamp(
      mapRobotToAPI(headPose.roll, 'roll'),
      ROBOT_POSITION_RANGES.ROLL.min,
      ROBOT_POSITION_RANGES.ROLL.max
    ),
  };
}
