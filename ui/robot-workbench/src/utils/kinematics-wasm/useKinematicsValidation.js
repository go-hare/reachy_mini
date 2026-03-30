/**
 * 🦀 Kinematics Validation Hook
 *
 * Validates head poses using IK before sending commands.
 * Prevents the robot from going to unreachable positions.
 *
 * ## Usage
 * ```js
 * const { validatePose, lastValidPose } = useKinematicsValidation();
 *
 * // Before sending a command
 * const result = validatePose(headPose, bodyYaw);
 * if (result.valid) {
 *   sendCommand(result.headPose, antennas, result.bodyYaw);
 * }
 * ```
 */

import { useCallback, useRef } from 'react';
import { useFullKinematics } from './useFullKinematics';

/**
 * Hook for validating poses using IK
 */
export function useKinematicsValidation() {
  const {
    isReady,
    inverseKinematicsSafe,
    forwardKinematics,
    resetForwardKinematics,
    isValidAngles,
    clampJointAngles,
    HEAD_Z_OFFSET,
    DEG2RAD,
  } = useFullKinematics();

  // Store last valid pose for fallback
  const lastValidPoseRef = useRef({
    headPose: { x: 0, y: 0, z: 0, pitch: 0, yaw: 0, roll: 0 },
    bodyYaw: 0,
    angles: null,
  });

  /**
   * Validate a head pose using IK
   *
   * @param {Object} headPose - { x, y, z, pitch, yaw, roll } in meters/radians
   * @param {number} bodyYaw - Body yaw in radians
   * @param {Object} options - Validation options
   * @returns {Object} { valid, headPose, bodyYaw, angles, error }
   */
  const validatePose = useCallback(
    (headPose, bodyYaw = 0, options = {}) => {
      if (!isReady) {
        // WASM not ready - allow the pose but warn
        return {
          valid: true,
          headPose,
          bodyYaw,
          angles: null,
          warning: 'WASM not ready - pose not validated',
        };
      }

      const {
        maxRelativeYaw = 60 * DEG2RAD,
        maxBodyYaw = 160 * DEG2RAD,
        useFallback = true,
      } = options;

      try {
        // Convert headPose to position/euler arrays
        const position = [headPose.x, headPose.y, headPose.z];
        const euler = [headPose.roll, headPose.pitch, headPose.yaw];

        // Run IK with safety limits
        const ikResult = inverseKinematicsSafe(position, euler, bodyYaw, {
          maxRelativeYaw,
          maxBodyYaw,
        });

        if (!ikResult) {
          console.warn('🚫 IK returned null - pose unreachable');
          if (useFallback) {
            return {
              valid: false,
              ...lastValidPoseRef.current,
              error: 'IK failed - using last valid pose',
            };
          }
          return { valid: false, headPose, bodyYaw, angles: null, error: 'IK failed' };
        }

        const { bodyYaw: correctedBodyYaw, angles } = ikResult;

        // Validate the angles
        if (!isValidAngles(angles)) {
          console.warn('🚫 IK returned invalid angles:', angles);
          if (useFallback) {
            return {
              valid: false,
              ...lastValidPoseRef.current,
              error: 'Invalid angles - using last valid pose',
            };
          }
          return { valid: false, headPose, bodyYaw, angles, error: 'Invalid angles' };
        }

        // Clamp angles to limits
        const clampedAngles = clampJointAngles(angles);

        // Check if clamping changed the angles significantly (would mean pose was at limit)
        const wasClampedSignificantly = angles.some(
          (angle, i) => Math.abs(angle - clampedAngles[i]) > 0.01
        );

        if (wasClampedSignificantly) {
          console.warn('⚠️ Pose was at joint limits - angles were clamped');
        }

        // Success - update last valid pose
        lastValidPoseRef.current = {
          headPose,
          bodyYaw: correctedBodyYaw,
          angles: clampedAngles,
        };

        return {
          valid: true,
          headPose,
          bodyYaw: correctedBodyYaw,
          angles: clampedAngles,
          warning: wasClampedSignificantly ? 'Pose at joint limits' : null,
        };
      } catch (err) {
        console.error('❌ Validation error:', err);
        if (useFallback) {
          return {
            valid: false,
            ...lastValidPoseRef.current,
            error: err.message,
          };
        }
        return { valid: false, headPose, bodyYaw, angles: null, error: err.message };
      }
    },
    [isReady, inverseKinematicsSafe, isValidAngles, clampJointAngles, DEG2RAD]
  );

  /**
   * Compute pose from angles (FK) and validate
   *
   * @param {number[]} angles - [stewart_1, ..., stewart_6]
   * @param {number} bodyYaw - Body yaw in radians
   * @returns {Object} { valid, headPose, bodyYaw }
   */
  const validateAngles = useCallback(
    (angles, bodyYaw = 0) => {
      if (!isReady) {
        return { valid: false, error: 'WASM not ready' };
      }

      try {
        if (!isValidAngles(angles)) {
          return { valid: false, error: 'Invalid angles' };
        }

        const clampedAngles = clampJointAngles(angles);

        // Reset FK state
        resetForwardKinematics([0, 0, HEAD_Z_OFFSET], [0, 0, 0]);

        // Run FK
        const fkResult = forwardKinematics(clampedAngles, bodyYaw);
        if (!fkResult) {
          return { valid: false, error: 'FK failed' };
        }

        const { position, euler } = fkResult;

        return {
          valid: true,
          headPose: {
            x: position[0],
            y: position[1],
            z: position[2],
            roll: euler[0],
            pitch: euler[1],
            yaw: euler[2],
          },
          bodyYaw,
          angles: clampedAngles,
        };
      } catch (err) {
        return { valid: false, error: err.message };
      }
    },
    [
      isReady,
      isValidAngles,
      clampJointAngles,
      forwardKinematics,
      resetForwardKinematics,
      HEAD_Z_OFFSET,
    ]
  );

  /**
   * Get the last valid pose (for fallback)
   */
  const getLastValidPose = useCallback(() => {
    return { ...lastValidPoseRef.current };
  }, []);

  /**
   * Set the last valid pose (e.g., when receiving from robot)
   */
  const setLastValidPose = useCallback((headPose, bodyYaw, angles = null) => {
    lastValidPoseRef.current = { headPose, bodyYaw, angles };
  }, []);

  return {
    isReady,
    validatePose,
    validateAngles,
    getLastValidPose,
    setLastValidPose,
  };
}

export default useKinematicsValidation;
