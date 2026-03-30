import { useRef, useEffect, useLayoutEffect, useState, memo } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import robotModelCache from '../../utils/robotModelCache';
import useAppStore from '../../store/useAppStore';
import { logInfo } from '../../utils/logging';
import { applyRobotMaterials } from '../../utils/viewer3d/applyRobotMaterials';
import { STEWART_JOINT_NAMES, PASSIVE_JOINT_NAMES } from '../../constants/robotBuffer';

/**
 * Robot component loaded from local URDF
 * Loads assets from /assets/robot-3d/ instead of daemon
 * Manages 3D model loading, head and antenna animations
 */
function URDFRobot({
  headJoints, // Array of 7 values [yaw_body, stewart_1, ..., stewart_6]
  passiveJoints, // Array of 21 values (optional, only if Placo active)
  yawBody,
  antennas,
  isActive,
  isTransparent,
  xrayOpacity = 0.5,
  wireframe = false,
  onMeshesReady,
  onRobotReady,
  forceLoad = false,
  dataVersion = 0,
}) {
  const [robot, setRobot] = useState(null);
  const [isReady, setIsReady] = useState(false);
  const groupRef = useRef();
  const meshesRef = useRef([]);
  const displayTimeoutRef = useRef(null);
  const { camera, gl } = useThree();
  const darkMode = useAppStore(state => state.darkMode);
  const robotStateFull = useAppStore(state => state.robotStateFull);

  // Capture initial robot position once (for immediate display without flicker)
  // 🔄 Reset when robotStateFull.data becomes null (robot switch cleanup)
  const robotStateFullRef = useRef(null);
  if (!robotStateFull?.data) {
    // Robot disconnected/switched - clear cached state
    robotStateFullRef.current = null;
  } else if (!robotStateFullRef.current && robotStateFull?.data?.head_joints) {
    // Capture initial position
    robotStateFullRef.current = robotStateFull;
  }
  const raycaster = useRef(new THREE.Raycaster());
  const mouse = useRef(new THREE.Vector2());
  const frameCountRef = useRef(0);
  const lastClickTimeRef = useRef(0);
  const clickThrottleMs = 300;
  const lastAppliedVersionRef = useRef(-1);

  // Mouse/click handlers for raycaster interaction
  useEffect(() => {
    const handleMouseMove = event => {
      const rect = gl.domElement.getBoundingClientRect();
      mouse.current.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.current.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    };

    const handleClick = event => {
      if (!robot) return;

      const now = Date.now();
      if (now - lastClickTimeRef.current < clickThrottleMs) return;
      lastClickTimeRef.current = now;

      requestAnimationFrame(() => {
        const rect = gl.domElement.getBoundingClientRect();
        const x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        const y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        raycaster.current.setFromCamera(new THREE.Vector2(x, y), camera);
        const intersects = raycaster.current.intersectObject(robot, true);

        if (intersects.length > 0) {
          const mesh = intersects[0].object;
          if (mesh.isMesh && !mesh.userData.isErrorMesh) {
            const messages = [
              '👆 You clicked on Reachy!',
              '🤖 That tickles!',
              '✨ Nice aim!',
              '🎯 Bullseye!',
              '👋 Hey there!',
            ];
            logInfo(messages[Math.floor(Math.random() * messages.length)]);
          }
        }
      });
    };

    gl.domElement.addEventListener('mousemove', handleMouseMove);
    gl.domElement.addEventListener('click', handleClick);
    return () => {
      gl.domElement.removeEventListener('mousemove', handleMouseMove);
      gl.domElement.removeEventListener('click', handleClick);
    };
  }, [gl, camera, robot]);

  // Load URDF model from cache
  useEffect(() => {
    if (!isActive && !forceLoad) {
      setRobot(null);
      setIsReady(false);
      return;
    }

    let isMounted = true;

    robotModelCache
      .getModel()
      .then(cachedModel => {
        if (!isMounted) return;

        const robotModel = cachedModel.clone(true);

        // Collect and notify meshes
        const collectedMeshes = [];
        robotModel.traverse(child => {
          if (child.isMesh) collectedMeshes.push(child);
        });
        meshesRef.current = collectedMeshes;
        onMeshesReady?.(collectedMeshes);
        onRobotReady?.(robotModel);

        // Apply initial joints if available
        if (robotModel?.joints) {
          const initialJoints = robotStateFullRef.current?.data?.head_joints;
          const hasValidInitialJoints = Array.isArray(initialJoints) && initialJoints.length === 7;

          if (hasValidInitialJoints) {
            // Apply from daemon state
            if (robotModel.joints['yaw_body']) {
              robotModel.setJointValue('yaw_body', initialJoints[0]);
            }
            STEWART_JOINT_NAMES.forEach((jointName, index) => {
              if (robotModel.joints[jointName]) {
                robotModel.setJointValue(jointName, initialJoints[index + 1]);
              }
            });

            // Apply antennas (inverted mapping)
            const initialAntennas = robotStateFullRef.current?.data?.antennas;
            if (Array.isArray(initialAntennas) && initialAntennas.length === 2) {
              if (robotModel.joints['left_antenna']) {
                robotModel.setJointValue('left_antenna', -initialAntennas[1]);
              }
              if (robotModel.joints['right_antenna']) {
                robotModel.setJointValue('right_antenna', -initialAntennas[0]);
              }
            }
          } else {
            // Fallback: initialize to zero
            if (robotModel.joints['yaw_body']) {
              robotModel.setJointValue('yaw_body', 0);
            }
            STEWART_JOINT_NAMES.forEach(jointName => {
              if (robotModel.joints[jointName]) {
                robotModel.setJointValue(jointName, 0);
              }
            });
          }

          // 🎯 Initialize passive joints from store data if available (prevents flicker)
          const initialPassiveJoints = robotStateFullRef.current?.data?.passive_joints;
          const hasValidPassiveJoints =
            Array.isArray(initialPassiveJoints) && initialPassiveJoints.length >= 21;

          if (hasValidPassiveJoints) {
            // Apply from daemon state
            for (let i = 0; i < 21; i++) {
              const jointName = PASSIVE_JOINT_NAMES[i];
              if (robotModel.joints[jointName]) {
                robotModel.setJointValue(jointName, initialPassiveJoints[i]);
              }
            }
          } else {
            // Fallback: initialize to 0
            PASSIVE_JOINT_NAMES.forEach(jointName => {
              if (robotModel.joints[jointName]) {
                robotModel.setJointValue(jointName, 0);
              }
            });
          }

          // Force matrix update
          robotModel.traverse(child => {
            if (child.isObject3D) {
              child.updateMatrix();
              child.updateMatrixWorld(true);
            }
          });

          // Display immediately if we have initial position
          if (hasValidInitialJoints) {
            if (!isMounted) return;
            setRobot(robotModel);
            return;
          }
        }

        // Fallback: wait 500ms before displaying
        displayTimeoutRef.current = setTimeout(() => {
          if (!isMounted) return;
          setRobot(robotModel);
          displayTimeoutRef.current = null;
        }, 500);
      })
      .catch(err => {
        console.error('URDF loading error:', err);
      });

    return () => {
      isMounted = false;
      if (displayTimeoutRef.current) {
        clearTimeout(displayTimeoutRef.current);
        displayTimeoutRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive, forceLoad, onMeshesReady]);

  // Animation loop (~20 Hz throttled)
  useFrame(() => {
    if (!robot) return;
    if (!isActive && !forceLoad) return;

    // Throttle to ~20 Hz (every 3rd frame at 60 FPS)
    frameCountRef.current++;
    if (frameCountRef.current % 3 !== 0) return;

    // Skip if no new data
    if (dataVersion === lastAppliedVersionRef.current) return;
    lastAppliedVersionRef.current = dataVersion;

    // Apply head joints
    if (headJoints && Array.isArray(headJoints) && headJoints.length === 7) {
      if (robot.joints['yaw_body']) robot.setJointValue('yaw_body', headJoints[0]);
      if (robot.joints['stewart_1']) robot.setJointValue('stewart_1', headJoints[1]);
      if (robot.joints['stewart_2']) robot.setJointValue('stewart_2', headJoints[2]);
      if (robot.joints['stewart_3']) robot.setJointValue('stewart_3', headJoints[3]);
      if (robot.joints['stewart_4']) robot.setJointValue('stewart_4', headJoints[4]);
      if (robot.joints['stewart_5']) robot.setJointValue('stewart_5', headJoints[5]);
      if (robot.joints['stewart_6']) robot.setJointValue('stewart_6', headJoints[6]);
    } else if (yawBody !== undefined && robot.joints['yaw_body']) {
      robot.setJointValue('yaw_body', yawBody);
    }

    // Apply passive joints
    if (passiveJoints) {
      const passiveArray = Array.isArray(passiveJoints) ? passiveJoints : passiveJoints.array;
      if (passiveArray && passiveArray.length >= 21) {
        for (let i = 0; i < 21; i++) {
          const jointName = PASSIVE_JOINT_NAMES[i];
          if (robot.joints[jointName]) {
            robot.setJointValue(jointName, passiveArray[i]);
          }
        }
      }
    }

    // Apply antennas (inverted mapping for correct visual representation)
    if (antennas && Array.isArray(antennas) && antennas.length >= 2) {
      if (robot.joints['left_antenna']) robot.setJointValue('left_antenna', -antennas[1]);
      if (robot.joints['right_antenna']) robot.setJointValue('right_antenna', -antennas[0]);
    }
  });

  // Apply materials (useLayoutEffect prevents flash)
  useLayoutEffect(() => {
    if (!robot) return;

    applyRobotMaterials(robot, {
      transparent: isTransparent,
      wireframe,
      xrayOpacity,
      darkMode,
    });

    if (!isReady) setIsReady(true);
  }, [robot, isTransparent, xrayOpacity, wireframe, darkMode, isReady]);

  return robot && isReady ? (
    <group position={[0, 0, 0]} rotation={[0, -Math.PI / 2, 0]}>
      <primitive ref={groupRef} object={robot} scale={1} rotation={[-Math.PI / 2, 0, 0]} />
    </group>
  ) : null;
}

// Memoize URDFRobot - use dataVersion for O(1) comparison instead of array diffs
const URDFRobotMemo = memo(URDFRobot, (prevProps, nextProps) => {
  // Re-render on visual/state prop changes
  if (
    prevProps.isActive !== nextProps.isActive ||
    prevProps.isTransparent !== nextProps.isTransparent ||
    prevProps.wireframe !== nextProps.wireframe ||
    prevProps.forceLoad !== nextProps.forceLoad ||
    prevProps.xrayOpacity !== nextProps.xrayOpacity ||
    prevProps.dataVersion !== nextProps.dataVersion
  ) {
    return false;
  }
  return true;
});

export default URDFRobotMemo;
