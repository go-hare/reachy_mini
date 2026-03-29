import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import URDFLoader from "urdf-loader";

import urdfFile from "@/assets/robot-3d/reachy-mini.urdf?raw";
import type {
  ReachyConnectionState,
  ReachyFullState,
} from "@/lib/reachy-daemon";

const STEWART_JOINT_NAMES = [
  "stewart_1",
  "stewart_2",
  "stewart_3",
  "stewart_4",
  "stewart_5",
  "stewart_6",
] as const;

const PASSIVE_JOINT_NAMES = [
  "passive_1_x",
  "passive_1_y",
  "passive_1_z",
  "passive_2_x",
  "passive_2_y",
  "passive_2_z",
  "passive_3_x",
  "passive_3_y",
  "passive_3_z",
  "passive_4_x",
  "passive_4_y",
  "passive_4_z",
  "passive_5_x",
  "passive_5_y",
  "passive_5_z",
  "passive_6_x",
  "passive_6_y",
  "passive_6_z",
  "passive_7_x",
  "passive_7_y",
  "passive_7_z",
] as const;

type UrdfRobotObject = THREE.Object3D & {
  joints?: Record<string, { setJointValue: (value: number) => void }>;
};

type ReachyKinematicsWasmModule = {
  default: () => Promise<unknown>;
  calculate_passive_joints: (
    headJoints: Float64Array,
    headPose: Float64Array,
  ) => Float64Array;
};

type ReachyMatrixPose = {
  m: number[];
};

let reachyKinematicsWasmModule: ReachyKinematicsWasmModule | null = null;
let reachyKinematicsWasmPromise:
  | Promise<ReachyKinematicsWasmModule>
  | null = null;

async function loadReachyKinematicsWasm() {
  if (reachyKinematicsWasmModule) {
    return reachyKinematicsWasmModule;
  }

  if (reachyKinematicsWasmPromise) {
    return reachyKinematicsWasmPromise;
  }

  reachyKinematicsWasmPromise = import(
    "@/lib/kinematics-wasm/reachy_mini_kinematics_wasm.js"
  ).then(async (module) => {
    const typedModule = module as ReachyKinematicsWasmModule;
    await typedModule.default();
    reachyKinematicsWasmModule = typedModule;
    return typedModule;
  });

  return reachyKinematicsWasmPromise;
}

class RobotModelCache {
  private model: UrdfRobotObject | null = null;
  private loadPromise: Promise<UrdfRobotObject> | null = null;

  async getModel(): Promise<UrdfRobotObject> {
    if (this.model) {
      return this.model;
    }

    if (this.loadPromise) {
      return this.loadPromise;
    }

    this.loadPromise = (async () => {
      const loader = new URDFLoader();
      const stlFileMap = new Map<string, string>();

      loader.manager.setURLModifier((url) => {
        const filename = url.split("/").pop();
        const localUrl = new URL(
          `../../assets/robot-3d/meshes/${filename}`,
          import.meta.url,
        ).href;
        if (filename) {
          stlFileMap.set(url, filename);
          stlFileMap.set(localUrl, filename);
        }
        return localUrl;
      });

      const parsedModel = loader.parse(urdfFile) as UrdfRobotObject;

      await new Promise<void>((resolve) => {
        let resolved = false;
        const finish = () => {
          if (resolved) return;
          resolved = true;
          resolve();
        };

        const originalOnLoad = loader.manager.onLoad;
        loader.manager.onLoad = () => {
          originalOnLoad?.();
          finish();
        };

        window.setTimeout(finish, 2_000);
      });

      parsedModel.traverse((child) => {
        if (!(child instanceof THREE.Mesh)) return;

        child.castShadow = true;
        child.receiveShadow = true;

        if (child.geometry.getAttribute("normal")) {
          child.geometry.deleteAttribute("normal");
        }
        child.geometry.computeVertexNormals();

        const sourceMaterial = Array.isArray(child.material)
          ? child.material[0]
          : child.material;
        const sourceColor =
          sourceMaterial instanceof THREE.Material &&
          "color" in sourceMaterial &&
          sourceMaterial.color instanceof THREE.Color
            ? sourceMaterial.color
            : null;

        const material = new THREE.MeshStandardMaterial({
          color: sourceColor?.clone() ?? new THREE.Color("#f2f2f2"),
          flatShading: true,
          metalness: 0,
          roughness: 0.72,
        });

        const stlFileName = [
          child.geometry.userData?.url,
          child.geometry.userData?.sourceFile,
          child.geometry.userData?.filename,
          child.geometry.userData?.sourceURL,
        ]
          .map((value) =>
            typeof value === "string"
              ? stlFileMap.get(value) || value.split("/").pop()
              : null,
          )
          .find((value): value is string => Boolean(value));

        if (stlFileName) {
          child.userData.stlFileName = stlFileName;
        }

        child.material = material;
        child.material.needsUpdate = true;
      });

      parsedModel.traverse((child) => {
        if (!(child instanceof THREE.Object3D)) return;
        child.updateMatrix();
        child.updateMatrixWorld(true);
      });

      this.model = parsedModel;
      return parsedModel;
    })();

    return this.loadPromise;
  }
}

const robotModelCache = new RobotModelCache();

function isXYZRPYPose(
  pose: unknown,
): pose is {
  x: number;
  y: number;
  z: number;
  roll: number;
  pitch: number;
  yaw: number;
} {
  if (!pose || typeof pose !== "object" || Array.isArray(pose)) return false;

  const candidate = pose as Record<string, unknown>;
  return (
    typeof candidate.x === "number" &&
    typeof candidate.y === "number" &&
    typeof candidate.z === "number" &&
    typeof candidate.roll === "number" &&
    typeof candidate.pitch === "number" &&
    typeof candidate.yaw === "number"
  );
}

function isMatrixPose(pose: unknown): pose is ReachyMatrixPose {
  if (!pose || typeof pose !== "object" || Array.isArray(pose)) return false;

  const candidate = pose as Record<string, unknown>;
  return (
    Array.isArray(candidate.m) &&
    candidate.m.length === 16 &&
    candidate.m.every((value) => typeof value === "number")
  );
}

function toRowMajorPoseMatrix(pose: unknown): number[] | null {
  if (Array.isArray(pose) && pose.length === 16) {
    return pose.every((value) => typeof value === "number")
      ? [...pose]
      : null;
  }

  if (isMatrixPose(pose)) {
    return [...pose.m];
  }

  if (!isXYZRPYPose(pose)) {
    return null;
  }

  const quaternion = new THREE.Quaternion().setFromEuler(
    new THREE.Euler(pose.roll, pose.pitch, pose.yaw, "XYZ"),
  );
  const matrix = new THREE.Matrix4().compose(
    new THREE.Vector3(pose.x, pose.y, pose.z),
    quaternion,
    new THREE.Vector3(1, 1, 1),
  );
  const e = matrix.elements;

  return [
    e[0],
    e[4],
    e[8],
    e[12],
    e[1],
    e[5],
    e[9],
    e[13],
    e[2],
    e[6],
    e[10],
    e[14],
    e[3],
    e[7],
    e[11],
    e[15],
  ];
}

function updateJointIfPresent(
  robot: UrdfRobotObject,
  jointName: string,
  value: number | null | undefined,
) {
  if (typeof value !== "number" || Number.isNaN(value)) return;
  robot.joints?.[jointName]?.setJointValue(value);
}

function applyRobotPose(
  robot: UrdfRobotObject,
  snapshot?: ReachyFullState | null,
) {
  if (!snapshot) return;

  const headJoints = Array.isArray(snapshot.head_joints)
    ? snapshot.head_joints
    : null;
  const passiveJoints = Array.isArray(snapshot.passive_joints)
    ? snapshot.passive_joints
    : null;
  const antennas = Array.isArray(snapshot.antennas_position)
    ? snapshot.antennas_position
    : null;

  if (headJoints && headJoints.length >= 7) {
    updateJointIfPresent(robot, "yaw_body", headJoints[0]);
    STEWART_JOINT_NAMES.forEach((jointName, index) => {
      updateJointIfPresent(robot, jointName, headJoints[index + 1]);
    });
  } else {
    updateJointIfPresent(robot, "yaw_body", snapshot.body_yaw);
  }

  if (passiveJoints && passiveJoints.length >= PASSIVE_JOINT_NAMES.length) {
    PASSIVE_JOINT_NAMES.forEach((jointName, index) => {
      updateJointIfPresent(robot, jointName, passiveJoints[index]);
    });
  }

  if (antennas && antennas.length >= 2) {
    updateJointIfPresent(robot, "left_antenna", -antennas[1]);
    updateJointIfPresent(robot, "right_antenna", -antennas[0]);
  }
}

function RobotModel({ snapshot }: { snapshot?: ReachyFullState | null }) {
  const [robot, setRobot] = useState<UrdfRobotObject | null>(null);

  useEffect(() => {
    let cancelled = false;

    void robotModelCache.getModel().then((baseModel) => {
      if (cancelled) return;

      const nextRobot = baseModel.clone(true) as UrdfRobotObject;
      applyRobotPose(nextRobot, snapshot);
      setRobot(nextRobot);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!robot) return;
    applyRobotPose(robot, snapshot);
  }, [robot, snapshot]);

  if (!robot) return null;

  return (
    <group position={[0, 0, 0]} rotation={[0, -Math.PI / 2, 0]}>
      <primitive object={robot} rotation={[-Math.PI / 2, 0, 0]} />
    </group>
  );
}

function getViewportBadgeLabel(
  connectionState: ReachyConnectionState,
  runtimeRunning: boolean,
) {
  if (connectionState === "live") return "Live Pose";
  if (runtimeRunning) return "Streaming Soon";
  return "Ready";
}

type ReachySimulationViewportSize = "compact" | "immersive";

function getViewportContainerClassName(
  size: ReachySimulationViewportSize,
  backgroundClassName: string,
) {
  const heightClassName =
    size === "immersive"
      ? "aspect-[4/3] min-h-[250px]"
      : "h-[220px]";

  return `relative ${heightClassName} overflow-hidden rounded-2xl border border-border/60 ${backgroundClassName}`;
}

export function ReachySimulationViewport({
  snapshot,
  connectionState,
  runtimeRunning,
  size = "compact",
}: {
  snapshot?: ReachyFullState | null;
  connectionState: ReachyConnectionState;
  runtimeRunning: boolean;
  size?: ReachySimulationViewportSize;
}) {
  const isJsdom =
    typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);
  const [kinematicsWasm, setKinematicsWasm] =
    useState<ReachyKinematicsWasmModule | null>(null);
  const badgeLabel = useMemo(
    () => getViewportBadgeLabel(connectionState, runtimeRunning),
    [connectionState, runtimeRunning],
  );
  const resolvedPassiveJoints = useMemo(() => {
    if (Array.isArray(snapshot?.passive_joints)) {
      return snapshot.passive_joints;
    }

    if (!snapshot || !kinematicsWasm) {
      return null;
    }

    const headJoints =
      Array.isArray(snapshot.head_joints) && snapshot.head_joints.length >= 7
        ? snapshot.head_joints.slice(0, 7)
        : null;
    const headPoseMatrix = toRowMajorPoseMatrix(snapshot.head_pose);

    if (!headJoints || !headPoseMatrix) {
      return null;
    }

    try {
      const result = kinematicsWasm.calculate_passive_joints(
        new Float64Array(headJoints),
        new Float64Array(headPoseMatrix),
      );
      return result.length >= PASSIVE_JOINT_NAMES.length
        ? Array.from(result)
        : null;
    } catch {
      return null;
    }
  }, [kinematicsWasm, snapshot]);
  const effectiveSnapshot = useMemo(() => {
    if (!snapshot) return null;

    return {
      ...snapshot,
      passive_joints: resolvedPassiveJoints,
    };
  }, [resolvedPassiveJoints, snapshot]);

  useEffect(() => {
    if (isJsdom) {
      return;
    }

    let cancelled = false;

    void loadReachyKinematicsWasm()
      .then((module) => {
        if (cancelled) return;
        setKinematicsWasm(module);
      })
      .catch(() => {
        if (cancelled) return;
        setKinematicsWasm(null);
      });

    return () => {
      cancelled = true;
    };
  }, [isJsdom]);

  if (isJsdom) {
    return (
      <div
        className={getViewportContainerClassName(size, "bg-background")}
        data-testid="reachy-simulation-viewport"
      >
        <div className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
          Reachy 3D simulation viewport
        </div>
      </div>
    );
  }

  return (
    <div
      className={getViewportContainerClassName(size, "bg-[#161616]")}
      data-testid="reachy-simulation-viewport"
    >
      <Canvas
        camera={{ position: [-0.25, 0.35, 0.55], fov: 50 }}
        dpr={[1, 1.75]}
        shadows
        gl={{
          antialias: true,
          alpha: false,
          preserveDrawingBuffer: true,
          powerPreference: "high-performance",
        }}
      >
        <color attach="background" args={["#161616"]} />
        <fog attach="fog" args={["#161616", 0.85, 2.4]} />
        <ambientLight intensity={0.55} />
        <directionalLight
          castShadow
          intensity={1.9}
          position={[2, 4, 2]}
          shadow-mapSize-width={1024}
          shadow-mapSize-height={1024}
        />
        <directionalLight intensity={0.45} position={[-2, 2, 1.5]} />
        <directionalLight intensity={0.7} color="#ffb45e" position={[0, 3, -2]} />
        <gridHelper
          args={[1.2, 12, new THREE.Color("#3a3a3a"), new THREE.Color("#242424")]}
          position={[0, 0, 0]}
        />
        <RobotModel snapshot={effectiveSnapshot} />
        <OrbitControls
          enablePan={false}
          enableZoom
          enableRotate
          enableDamping
          dampingFactor={0.06}
          target={[0, 0.2, 0]}
          minDistance={0.2}
          maxDistance={0.6}
        />
      </Canvas>

      <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded-full border border-[rgba(255,171,64,0.35)] bg-[rgba(17,17,17,0.78)] px-3 py-1 text-[11px] font-medium uppercase tracking-[0.12em] text-[#ffb24d] backdrop-blur-sm">
        Embedded 3D
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-full border border-[rgba(74,222,128,0.22)] bg-[rgba(17,17,17,0.78)] px-3 py-1 text-[11px] font-medium text-[#b7f5c5] backdrop-blur-sm">
        {badgeLabel}
      </div>
    </div>
  );
}

export default ReachySimulationViewport;
