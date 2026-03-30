/**
 * 🦀 Full Kinematics WASM Hook
 *
 * Complete kinematics solver running in WebAssembly:
 * - Inverse Kinematics (IK): pose → joint angles
 * - Forward Kinematics (FK): joint angles → pose
 * - Passive Joints: calculate ball joint angles for 3D visualization
 * - Safety: clamp angles, validate solutions
 *
 * ## Usage
 * ```js
 * const {
 *   isReady,
 *   inverseKinematics,
 *   inverseKinematicsSafe,
 *   forwardKinematics,
 *   calculatePassiveJoints,
 *   clampJointAngles,
 *   isValidAngles,
 * } = useFullKinematics();
 * ```
 *
 * @see reachy_mini_rust_kinematics for the Rust source
 */

import { useState, useEffect, useRef, useCallback } from 'react';

// Kinematics data (from reachy_mini/assets/kinematics_data.json)
const KINEMATICS_DATA = {
  motor_arm_length: 0.04,
  rod_length: 0.085,
  head_z_offset: 0.177,
  motors: [
    {
      name: 'stewart_1',
      branch_position: [0.020648178337122566, 0.021763723638894568, 1.0345743467476964e-7],
      T_motor_world: [
        [0.8660247915798898, -0.5000010603626028, -2.298079077119539e-6, -0.009999848080267933],
        [4.490195936008854e-6, 3.1810770986818273e-6, 0.999999999984859, -0.07663346037245178],
        [-0.500001060347722, -0.8660247915770963, 4.999994360718464e-6, 0.03666015757925319],
        [0.0, 0.0, 0.0, 1.0],
      ],
      solution: 0,
      limits: [-Math.PI, Math.PI],
    },
    {
      name: 'stewart_2',
      branch_position: [0.00852381571767217, 0.028763668526131346, 1.183437210727778e-7],
      T_motor_world: [
        [-0.8660211183436269, 0.5000074225224785, 2.298069723064582e-6, -0.01000055227585102],
        [-4.490219645842903e-6, -3.181063409649239e-6, -0.999999999984859, 0.07663346037219607],
        [-0.5000074225075973, -0.8660211183408337, 5.00001124330122e-6, 0.03666008712637943],
        [0.0, 0.0, 0.0, 1.0],
      ],
      solution: 1,
      limits: [-Math.PI, Math.PI],
    },
    {
      name: 'stewart_3',
      branch_position: [-0.029172011376922807, 0.0069999429399361995, 4.0290270064691214e-8],
      T_motor_world: [
        [6.326794896519466e-6, 0.9999999999799852, -7.0550646912150425e-12, -0.009999884140839245],
        [-1.0196153102346142e-6, 1.3505961633338446e-11, 0.9999999999994795, -0.07663346037438698],
        [0.9999999999794655, -6.326794896940104e-6, 1.0196153098685706e-6, 0.036660683387545835],
        [0.0, 0.0, 0.0, 1.0],
      ],
      solution: 0,
      limits: [-Math.PI, Math.PI],
    },
    {
      name: 'stewart_4',
      branch_position: [-0.029172040355214434, -0.0069999960097160766, -3.1608172912367394e-8],
      T_motor_world: [
        [-3.673205069955933e-6, -0.9999999999932537, -6.767968877969483e-14, -0.010000000000897517],
        [1.0196153102837198e-6, -3.6775764393585005e-12, -0.9999999999994795, 0.0766334603742898],
        [0.9999999999927336, -3.673205070385213e-6, 1.0196153102903487e-6, 0.03666065685180194],
        [0.0, 0.0, 0.0, 1.0],
      ],
      solution: 1,
      limits: [-Math.PI, Math.PI],
    },
    {
      name: 'stewart_5',
      branch_position: [0.008523809101930114, -0.028763713010385224, -1.4344916837716326e-7],
      T_motor_world: [
        [-0.8660284647694133, -0.4999946981757419, 2.298079429767357e-6, -0.010000231529504576],
        [4.490172883391843e-6, -3.1811099293773187e-6, 0.9999999999848591, -0.07663346037246624],
        [-0.4999946981608617, 0.8660284647666201, 4.999994384073154e-6, 0.03666016059492482],
        [0.0, 0.0, 0.0, 1.0],
      ],
      solution: 0,
      limits: [-Math.PI, Math.PI],
    },
    {
      name: 'stewart_6',
      branch_position: [0.020648186722822436, -0.02176369606185343, -8.957920105689965e-8],
      T_motor_world: [
        [0.8660247915798897, 0.5000010603626025, -2.298069644866714e-6, -0.009999527331574583],
        [-4.490196220318687e-6, 3.1810964558725514e-6, -0.9999999999848591, 0.07663346037272492],
        [-0.500001060347722, 0.8660247915770967, 5.000011266610794e-6, 0.036660231042625266],
        [0.0, 0.0, 0.0, 1.0],
      ],
      solution: 1,
      limits: [-Math.PI, Math.PI],
    },
  ],
};

// Constants
const DEG2RAD = Math.PI / 180;
const HEAD_Z_OFFSET = 0.177;

// Default safety limits
const DEFAULT_MAX_RELATIVE_YAW = 60 * DEG2RAD; // 60 degrees
const DEFAULT_MAX_BODY_YAW = 160 * DEG2RAD; // 160 degrees

// Singleton WASM module
let wasmModule = null;
let wasmLoading = false;
let wasmLoadPromise = null;
let wasmInitialized = false;

/**
 * Load and initialize the WASM module
 */
async function loadWasm() {
  if (wasmModule && wasmInitialized) return wasmModule;
  if (wasmLoading) return wasmLoadPromise;

  wasmLoading = true;
  wasmLoadPromise = (async () => {
    try {
      // Dynamic import of the new WASM module
      const wasm = await import('./reachy_mini_rust_kinematics.js');
      await wasm.default(); // Initialize WASM

      // Initialize kinematics with motor data
      wasm.init_kinematics(JSON.stringify(KINEMATICS_DATA));

      // Reset FK to home position
      const homeMatrix = new Float64Array([
        1,
        0,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        1,
        HEAD_Z_OFFSET,
        0,
        0,
        0,
        1,
      ]);
      wasm.reset_forward_kinematics(homeMatrix);

      wasmModule = wasm;
      wasmInitialized = true;

      return wasm;
    } catch (err) {
      console.error('❌ Failed to load Full Kinematics WASM:', err);
      wasmLoading = false;
      throw err;
    }
  })();

  return wasmLoadPromise;
}

/**
 * Create transformation matrix from position [x, y, z] and euler angles [roll, pitch, yaw]
 */
function poseToMatrix(position, euler) {
  const [x, y, z] = position;
  const [roll, pitch, yaw] = euler;

  const cr = Math.cos(roll);
  const sr = Math.sin(roll);
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);

  // ZYX euler rotation matrix (row-major)
  return new Float64Array([
    cy * cp,
    cy * sp * sr - sy * cr,
    cy * sp * cr + sy * sr,
    x,
    sy * cp,
    sy * sp * sr + cy * cr,
    sy * sp * cr - cy * sr,
    y,
    -sp,
    cp * sr,
    cp * cr,
    z,
    0,
    0,
    0,
    1,
  ]);
}

/**
 * Extract position and euler angles from transformation matrix
 */
function matrixToPose(matrix) {
  const position = [matrix[3], matrix[7], matrix[11]];

  // Extract euler angles (ZYX convention)
  const pitch = Math.asin(-matrix[8]);
  let roll, yaw;

  if (Math.cos(pitch) > 0.001) {
    roll = Math.atan2(matrix[9], matrix[10]);
    yaw = Math.atan2(matrix[4], matrix[0]);
  } else {
    // Gimbal lock
    roll = Math.atan2(-matrix[6], matrix[5]);
    yaw = 0;
  }

  return { position, euler: [roll, pitch, yaw] };
}

/**
 * Hook to use full kinematics (IK, FK, passive joints)
 */
export function useFullKinematics() {
  const [isReady, setIsReady] = useState(wasmInitialized);
  const [error, setError] = useState(null);
  const wasmRef = useRef(wasmModule);

  useEffect(() => {
    if (wasmInitialized) {
      wasmRef.current = wasmModule;
      setIsReady(true);
      return;
    }

    loadWasm()
      .then(wasm => {
        wasmRef.current = wasm;
        setIsReady(true);
      })
      .catch(err => {
        setError(err.message);
      });
  }, []);

  /**
   * Inverse kinematics: pose → joint angles
   *
   * @param {number[]} position - [x, y, z] in meters
   * @param {number[]} euler - [roll, pitch, yaw] in radians
   * @param {number} bodyYaw - Body yaw angle in radians
   * @returns {number[]|null} - [stewart_1, ..., stewart_6] or null if error
   */
  const inverseKinematics = useCallback((position, euler, bodyYaw = 0) => {
    if (!wasmRef.current) return null;

    try {
      const matrix = poseToMatrix(position, euler);
      const result = wasmRef.current.inverse_kinematics(matrix, bodyYaw);
      return Array.from(result);
    } catch (err) {
      console.error('❌ IK error:', err);
      return null;
    }
  }, []);

  /**
   * Safe inverse kinematics with limits
   *
   * @param {number[]} position - [x, y, z] in meters
   * @param {number[]} euler - [roll, pitch, yaw] in radians
   * @param {number} bodyYaw - Target body yaw in radians
   * @param {Object} options - Safety options
   * @returns {Object|null} - { bodyYaw, angles: [stewart_1, ..., stewart_6] } or null
   */
  const inverseKinematicsSafe = useCallback((position, euler, bodyYaw = 0, options = {}) => {
    if (!wasmRef.current) return null;

    const { maxRelativeYaw = DEFAULT_MAX_RELATIVE_YAW, maxBodyYaw = DEFAULT_MAX_BODY_YAW } =
      options;

    try {
      const matrix = poseToMatrix(position, euler);
      const result = wasmRef.current.inverse_kinematics_safe(
        matrix,
        bodyYaw,
        maxRelativeYaw,
        maxBodyYaw
      );

      if (!result || result.length < 7) return null;

      const resultArray = Array.from(result);
      return {
        bodyYaw: resultArray[0],
        angles: resultArray.slice(1),
      };
    } catch (err) {
      console.error('❌ Safe IK error:', err);
      return null;
    }
  }, []);

  /**
   * Forward kinematics: joint angles → pose
   *
   * @param {number[]} angles - [stewart_1, ..., stewart_6]
   * @param {number} bodyYaw - Body yaw in radians
   * @returns {Object|null} - { position: [x,y,z], euler: [roll,pitch,yaw], matrix: Float64Array }
   */
  const forwardKinematics = useCallback((angles, bodyYaw = 0) => {
    if (!wasmRef.current) return null;

    try {
      const anglesArray = new Float64Array(angles);
      const result = wasmRef.current.forward_kinematics(anglesArray, bodyYaw);

      if (!result || result.length < 16) return null;

      const matrix = Array.from(result);
      const { position, euler } = matrixToPose(matrix);

      return { position, euler, matrix };
    } catch (err) {
      console.error('❌ FK error:', err);
      return null;
    }
  }, []);

  /**
   * Reset forward kinematics state (call before FK iterations)
   *
   * @param {number[]} position - Initial position [x, y, z]
   * @param {number[]} euler - Initial euler angles [roll, pitch, yaw]
   */
  const resetForwardKinematics = useCallback(
    (position = [0, 0, HEAD_Z_OFFSET], euler = [0, 0, 0]) => {
      if (!wasmRef.current) return;

      try {
        const matrix = poseToMatrix(position, euler);
        wasmRef.current.reset_forward_kinematics(matrix);
      } catch (err) {
        console.error('❌ Reset FK error:', err);
      }
    },
    []
  );

  /**
   * Calculate passive joints for 3D visualization
   *
   * @param {number[]} headJoints - [yaw_body, stewart_1, ..., stewart_6]
   * @param {number[]} headPose - 4x4 matrix as 16 floats (row-major)
   * @returns {number[]|null} - [p1_x, p1_y, p1_z, ..., p7_x, p7_y, p7_z] (21 values)
   */
  const calculatePassiveJoints = useCallback((headJoints, headPose) => {
    if (!wasmRef.current) return null;

    try {
      const jointsArray = new Float64Array(headJoints);
      const poseArray = new Float64Array(headPose);
      const result = wasmRef.current.calculate_passive_joints_wasm(jointsArray, poseArray);
      return Array.from(result);
    } catch (err) {
      console.error('❌ Passive joints error:', err);
      return null;
    }
  }, []);

  /**
   * Clamp joint angles to their limits
   *
   * @param {number[]} angles - Joint angles to clamp
   * @returns {number[]} - Clamped angles
   */
  const clampJointAngles = useCallback(angles => {
    if (!wasmRef.current) {
      // Fallback: clamp to default limits
      return angles.map(a => Math.max(-Math.PI, Math.min(Math.PI, a)));
    }

    try {
      const anglesArray = new Float64Array(angles);
      const result = wasmRef.current.clamp_joint_angles(anglesArray);
      return Array.from(result);
    } catch (err) {
      console.error('❌ Clamp error:', err);
      return angles;
    }
  }, []);

  /**
   * Validate joint angles
   *
   * @param {number[]} angles - Joint angles to validate
   * @returns {boolean} - True if valid
   */
  const isValidAngles = useCallback(angles => {
    if (!wasmRef.current) {
      // Fallback validation
      const maxAngle = 2 * Math.PI;
      return (
        Array.isArray(angles) &&
        angles.length >= 6 &&
        angles.every(a => Number.isFinite(a) && Math.abs(a) < maxAngle)
      );
    }

    try {
      const anglesArray = new Float64Array(angles);
      return wasmRef.current.is_valid_angles(anglesArray);
    } catch (err) {
      console.error('❌ Validate error:', err);
      return false;
    }
  }, []);

  /**
   * Get motor limits
   */
  const getMotorLimits = useCallback(() => {
    return KINEMATICS_DATA.motors.map(m => m.limits);
  }, []);

  return {
    isReady,
    error,

    // Core kinematics
    inverseKinematics,
    inverseKinematicsSafe,
    forwardKinematics,
    resetForwardKinematics,

    // Passive joints
    calculatePassiveJoints,

    // Utilities
    clampJointAngles,
    isValidAngles,
    getMotorLimits,

    // Helpers
    poseToMatrix,
    matrixToPose,

    // Constants
    HEAD_Z_OFFSET,
    DEG2RAD,
  };
}

/**
 * Standalone async function for use outside React
 */
export async function getKinematicsModule() {
  return loadWasm();
}

export default useFullKinematics;
