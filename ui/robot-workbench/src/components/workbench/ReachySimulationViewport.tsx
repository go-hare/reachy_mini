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
      loader.manager.setURLModifier((url) => {
        const filename = url.split("/").pop();
        return new URL(
          `../../assets/robot-3d/meshes/${filename}`,
          import.meta.url,
        ).href;
      });

      const parsedModel = loader.parse(urdfFile) as UrdfRobotObject;
      parsedModel.traverse((child) => {
        if (!(child instanceof THREE.Mesh)) return;

        child.castShadow = true;
        child.receiveShadow = true;

        const material = new THREE.MeshStandardMaterial({
          color: (child.material as THREE.MeshStandardMaterial | undefined)?.color
            ?.clone()
            ?? new THREE.Color("#d9d9d9"),
          metalness: 0.08,
          roughness: 0.52,
        });

        child.material = material;
      });

      this.model = parsedModel;
      return parsedModel;
    })();

    return this.loadPromise;
  }
}

const robotModelCache = new RobotModelCache();

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
    <group position={[0, -0.04, 0]} rotation={[0, -Math.PI / 2, 0]}>
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

function getViewportHint(
  connectionState: ReachyConnectionState,
  runtimeRunning: boolean,
) {
  if (connectionState === "live") {
    return "仿真状态已接入，3D 视图会跟着机器人姿态实时更新。";
  }

  if (runtimeRunning) {
    return "仿真已启动，等待第一帧关节状态。";
  }

  return "点击上面的 Start Simulation，这里就会开始显示 3D 仿真姿态。";
}

export function ReachySimulationViewport({
  snapshot,
  connectionState,
  runtimeRunning,
}: {
  snapshot?: ReachyFullState | null;
  connectionState: ReachyConnectionState;
  runtimeRunning: boolean;
}) {
  const isJsdom =
    typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);
  const badgeLabel = useMemo(
    () => getViewportBadgeLabel(connectionState, runtimeRunning),
    [connectionState, runtimeRunning],
  );
  const hint = useMemo(
    () => getViewportHint(connectionState, runtimeRunning),
    [connectionState, runtimeRunning],
  );

  if (isJsdom) {
    return (
      <div
        className="relative h-[220px] overflow-hidden rounded-2xl border border-border/60 bg-background"
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
      className="relative h-[220px] overflow-hidden rounded-2xl border border-border/60 bg-[#161616]"
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
        <RobotModel snapshot={snapshot} />
        <OrbitControls
          enablePan={false}
          enableZoom
          enableRotate
          enableDamping
          dampingFactor={0.06}
          target={[0, 0.16, 0]}
          minDistance={0.24}
          maxDistance={0.8}
        />
      </Canvas>

      <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 rounded-full border border-[rgba(255,171,64,0.35)] bg-[rgba(17,17,17,0.78)] px-3 py-1 text-[11px] font-medium uppercase tracking-[0.12em] text-[#ffb24d] backdrop-blur-sm">
        Embedded 3D
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-full border border-[rgba(74,222,128,0.22)] bg-[rgba(17,17,17,0.78)] px-3 py-1 text-[11px] font-medium text-[#b7f5c5] backdrop-blur-sm">
        {badgeLabel}
      </div>
      <div className="pointer-events-none absolute bottom-3 right-3 max-w-[180px] rounded-2xl border border-white/10 bg-[rgba(17,17,17,0.72)] px-3 py-2 text-right text-[11px] leading-5 text-white/72 backdrop-blur-sm">
        {hint}
      </div>
    </div>
  );
}

export default ReachySimulationViewport;
