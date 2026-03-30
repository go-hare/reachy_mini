import { useEffect, useRef } from 'react';
import { useController, ControllerMode } from '../context/ControllerContext';
import { useActiveRobotContext } from '../../context';

// Tolerance for detecting significant changes (avoids micro-sync)
const SYNC_TOLERANCE = 0.01;
const MAJOR_CHANGE_TOLERANCE = 0.1; // Only sync for major external changes

/**
 * Simplified sync hook using the new controller context
 * Replaces the 234-line useRobotSync with clean, maintainable logic
 */
export function useControllerSync() {
  const { state, actions, smoother, isDragging, isActive } = useController();
  const { robotState } = useActiveRobotContext();
  const robotStateFull = robotState.robotStateFull;

  // Track last sync time
  const lastSyncTimeRef = useRef(0);

  useEffect(() => {
    if (!isActive || !robotStateFull?.data) return;

    const data = robotStateFull.data;
    if (!data.head_pose) return;

    // Extract robot values
    const robotValues = {
      headPose: {
        x: data.head_pose.x || 0,
        y: data.head_pose.y || 0,
        z: data.head_pose.z || 0,
        pitch: data.head_pose.pitch || 0,
        yaw: data.head_pose.yaw || 0,
        roll: data.head_pose.roll || 0,
      },
      bodyYaw: typeof data.body_yaw === 'number' ? data.body_yaw : 0,
      antennas: data.antennas_position || [0, 0],
    };

    // RULE 1: Never sync while user is interacting
    if (isDragging) return;

    // RULE 2: Never sync if user recently interacted (30s cooldown handled by reducer)
    if (state.mode !== ControllerMode.IDLE) return;

    // RULE 3: Check if enough time has passed since last interaction
    const timeSinceInteraction = Date.now() - state.lastInteractionTime;
    if (timeSinceInteraction < 30000) return;

    // RULE 4: Only sync if there's a MAJOR difference (external change)
    const hasMajorChange = checkMajorChange(state, robotValues);
    if (!hasMajorChange) return;

    // RULE 5: Don't sync if robot is already close to our targets
    const targetValues = smoother.getTargetValues();
    const isCloseToTarget = isCloseEnough(robotValues, targetValues, SYNC_TOLERANCE);
    if (isCloseToTarget) return;

    // All checks passed - sync from robot
    actions.syncFromRobot(robotValues);
    smoother.sync(robotValues);
    lastSyncTimeRef.current = Date.now();
  }, [isActive, robotStateFull, state, isDragging, actions, smoother]);
}

/**
 * Check if there's a major change (external robot movement)
 */
function checkMajorChange(state, robotValues) {
  const headDiff =
    Math.abs(state.headPose.x - robotValues.headPose.x) +
    Math.abs(state.headPose.y - robotValues.headPose.y) +
    Math.abs(state.headPose.z - robotValues.headPose.z) +
    Math.abs(state.headPose.pitch - robotValues.headPose.pitch) +
    Math.abs(state.headPose.yaw - robotValues.headPose.yaw) +
    Math.abs(state.headPose.roll - robotValues.headPose.roll);

  const bodyYawDiff = Math.abs(state.bodyYaw - robotValues.bodyYaw);

  const antennasDiff =
    Math.abs(state.antennas[0] - robotValues.antennas[0]) +
    Math.abs(state.antennas[1] - robotValues.antennas[1]);

  return (
    headDiff > MAJOR_CHANGE_TOLERANCE ||
    bodyYawDiff > MAJOR_CHANGE_TOLERANCE ||
    antennasDiff > MAJOR_CHANGE_TOLERANCE
  );
}

/**
 * Check if two value sets are close enough
 */
function isCloseEnough(values1, values2, tolerance) {
  const headClose =
    Math.abs(values1.headPose.x - values2.headPose.x) < tolerance &&
    Math.abs(values1.headPose.y - values2.headPose.y) < tolerance &&
    Math.abs(values1.headPose.z - values2.headPose.z) < tolerance &&
    Math.abs(values1.headPose.pitch - values2.headPose.pitch) < tolerance &&
    Math.abs(values1.headPose.yaw - values2.headPose.yaw) < tolerance &&
    Math.abs(values1.headPose.roll - values2.headPose.roll) < tolerance;

  const bodyYawClose = Math.abs(values1.bodyYaw - values2.bodyYaw) < tolerance;

  const antennasClose =
    Math.abs(values1.antennas[0] - values2.antennas[0]) < tolerance &&
    Math.abs(values1.antennas[1] - values2.antennas[1]) < tolerance;

  return headClose && bodyYawClose && antennasClose;
}
