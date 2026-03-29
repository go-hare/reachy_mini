import {
  Bot,
  Boxes,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Gamepad2,
  Mic,
  MicOff,
  Orbit,
  Play,
  RadioTower,
  RefreshCw,
  Sparkles,
  Square,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ComponentType,
  type ReactNode,
} from "react";
import packageJson from "../../../package.json";
import { useSettings } from "@/contexts/settings-context";
import ReachyController from "@/components/controller/ReachyController";
import WorkbenchCameraFeed from "@/components/workbench/WorkbenchCameraFeed";
import ReachySimulationViewport from "@/components/workbench/ReachySimulationViewport";
import { useMujocoStatus } from "@/hooks/use-mujoco-status";
import { useRobotDaemonProcess } from "@/hooks/use-robot-daemon-process";
import {
  useReachyStatus,
  type ReachyStatusResult,
} from "@/hooks/use-reachy-status";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
  getDefaultRobotWorkbenchSettings,
  type RobotDaemonProcessStatus,
  type ReachyDaemonStatus,
  type ReachyXYZRPYPose,
} from "@/lib/reachy-daemon";

const dockToggleButtonClassName =
  "absolute left-0 top-1/2 z-40 inline-flex h-14 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-border/80 bg-background/95 text-muted-foreground shadow-[0_10px_30px_rgba(15,23,42,0.14)] backdrop-blur-sm transition-colors hover:border-border hover:text-foreground";

const DEFAULT_ROBOT_PANEL_WIDTH = 360;
const MIN_ROBOT_PANEL_WIDTH = 320;
const MAX_ROBOT_PANEL_WIDTH = 640;
const ROBOT_PANEL_WIDTH_STORAGE_KEY = "robot-workbench:right-panel-width";

function clampRobotPanelWidth(width: number) {
  return Math.min(
    MAX_ROBOT_PANEL_WIDTH,
    Math.max(MIN_ROBOT_PANEL_WIDTH, width),
  );
}

function getInitialRobotPanelWidth() {
  if (typeof window === "undefined") {
    return DEFAULT_ROBOT_PANEL_WIDTH;
  }

  const storedWidth = window.localStorage.getItem(
    ROBOT_PANEL_WIDTH_STORAGE_KEY,
  );
  if (!storedWidth) {
    return DEFAULT_ROBOT_PANEL_WIDTH;
  }

  const parsed = Number(storedWidth);
  if (!Number.isFinite(parsed)) {
    return DEFAULT_ROBOT_PANEL_WIDTH;
  }

  return clampRobotPanelWidth(parsed);
}

interface RobotPanelCardProps {
  title: string;
  eyebrow: string;
  description?: string;
  status: string;
  statusTone?: "neutral" | "success";
  testId: string;
  children: ReactNode;
}

function RobotPanelCard({
  title,
  eyebrow,
  description,
  status,
  statusTone = "neutral",
  testId,
  children,
}: RobotPanelCardProps) {
  const statusClassName =
    statusTone === "success"
      ? "border-[hsl(var(--success))]/20 bg-[hsl(var(--success))]/10 text-[hsl(var(--success))]"
      : "border-border/70 bg-muted/45 text-muted-foreground";

  return (
    <section
      className="flex min-h-0 shrink-0 flex-col overflow-hidden rounded-2xl border border-border/70 bg-background/95 shadow-sm"
      data-testid={testId}
    >
      <div className="border-b border-border/70 px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              {eyebrow}
            </p>
            <h2 className="mt-1 text-sm font-semibold text-foreground">
              {title}
            </h2>
            {description ? (
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {description}
              </p>
            ) : null}
          </div>
          <span
            className={`inline-flex min-w-[72px] shrink-0 items-center justify-center whitespace-nowrap rounded-full border px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] ${statusClassName}`}
          >
            {status}
          </span>
        </div>
      </div>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

function MetricRow({
  icon: Icon,
  label,
  value,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-muted/25 px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <Icon className="size-4 shrink-0 text-muted-foreground" />
        <span className="truncate text-xs font-medium text-foreground">
          {label}
        </span>
      </div>
      <span className="min-w-0 truncate text-right text-[11px] font-medium text-muted-foreground">
        {value}
      </span>
    </div>
  );
}

function formatRadiansAsDegrees(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${((value * 180) / Math.PI).toFixed(1)}deg`;
}

function getEulerPose(pose: unknown): ReachyXYZRPYPose | null {
  if (!pose || typeof pose !== "object" || Array.isArray(pose)) return null;

  const maybePose = pose as Partial<ReachyXYZRPYPose>;
  if (
    typeof maybePose.x === "number" &&
    typeof maybePose.y === "number" &&
    typeof maybePose.z === "number" &&
    typeof maybePose.roll === "number" &&
    typeof maybePose.pitch === "number" &&
    typeof maybePose.yaw === "number"
  ) {
    return maybePose as ReachyXYZRPYPose;
  }

  return null;
}

function formatAntennaPair(positions?: number[] | null) {
  if (!Array.isArray(positions) || positions.length < 2) return "—";
  return `${formatRadiansAsDegrees(positions[0])} / ${formatRadiansAsDegrees(positions[1])}`;
}

function formatTimestamp(value?: string | null) {
  if (!value) return "—";

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;

  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatDaemonLabel(value?: string | null) {
  if (!value) return "—";

  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function getMujocoBackendLabel(daemonStatus?: ReachyDaemonStatus | null) {
  if (daemonStatus?.simulation_enabled) return "MuJoCo Runtime";
  if (daemonStatus?.mockup_sim_enabled) return "Mockup Simulator";
  return "Physical Robot";
}

function formatProcessLifecycle(lifecycle?: string | null) {
  return lifecycle === "running" ? "Running" : "Stopped";
}

const workbenchPanelVars = {
  "--background": "0 0% 100%",
  "--foreground": "222 47% 11%",
  "--muted": "210 40% 96%",
  "--muted-foreground": "215 16% 47%",
  "--border": "214 32% 91%",
  "--input": "214 32% 91%",
  "--card": "0 0% 100%",
  "--card-foreground": "222 47% 11%",
  "--accent": "210 40% 96%",
  "--accent-foreground": "222 47% 11%",
} as CSSProperties;

const workbenchShellStyle = {
  ...workbenchPanelVars,
  background: "transparent",
} as CSSProperties;

function getWorkbenchRobotName(daemonStatus?: ReachyDaemonStatus | null) {
  return daemonStatus?.robot_name
    ? formatDaemonLabel(daemonStatus.robot_name)
    : "Reachy Mini";
}

function getWorkbenchModeBadge(daemonStatus?: ReachyDaemonStatus | null) {
  if (daemonStatus?.simulation_enabled) return "Sim";
  if (daemonStatus?.wireless_version) return "WiFi";
  return "Robot";
}

function WorkbenchAudioCard({
  device,
  platform,
  value,
  muted,
  onToggleMuted,
  onValueChange,
  activeIcon: ActiveIcon,
  mutedIcon: MutedIcon,
  testId,
}: {
  device: string;
  platform: string;
  value: number;
  muted: boolean;
  onToggleMuted: () => void;
  onValueChange: (value: number) => void;
  activeIcon: ComponentType<{ className?: string }>;
  mutedIcon: ComponentType<{ className?: string }>;
  testId?: string;
}) {
  const Icon = muted ? MutedIcon : ActiveIcon;

  return (
    <div
      className="h-16 rounded-[14px] border border-slate-200 bg-white px-3 py-2 shadow-[0_12px_24px_rgba(15,23,42,0.04)]"
      data-testid={testId}
    >
      <div className="flex h-full items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[10px] font-medium text-slate-950">
            {device}
          </p>
          <p className="mt-0.5 truncate font-mono text-[9px] text-slate-400">
            {platform}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-slate-500 transition-colors hover:text-amber-600"
            onClick={onToggleMuted}
            aria-label={muted ? "Unmute preview control" : "Mute preview control"}
          >
            <Icon className="size-4" />
          </button>
          <div className="relative w-[96px]">
            <div className="absolute inset-y-1/2 left-0 right-0 h-[3px] -translate-y-1/2 rounded-full bg-slate-200" />
            <div
              className="absolute inset-y-1/2 left-0 h-[3px] -translate-y-1/2 rounded-full bg-amber-500"
              style={{ width: `${muted ? 0 : value}%` }}
            />
            <input
              type="range"
              min={0}
              max={100}
              value={value}
              onChange={(event) =>
                onValueChange(Number(event.currentTarget.value))
              }
              className="relative z-10 w-full accent-amber-500"
              aria-label="Audio preview level"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function WorkbenchAudioControls({
  robotName,
  devicePlatform,
  speakerVolume,
  microphoneVolume,
  speakerMuted,
  microphoneMuted,
  onToggleSpeakerMuted,
  onToggleMicrophoneMuted,
  onSpeakerVolumeChange,
  onMicrophoneVolumeChange,
  className,
}: {
  robotName: string;
  devicePlatform: string;
  speakerVolume: number;
  microphoneVolume: number;
  speakerMuted: boolean;
  microphoneMuted: boolean;
  onToggleSpeakerMuted: () => void;
  onToggleMicrophoneMuted: () => void;
  onSpeakerVolumeChange: (value: number) => void;
  onMicrophoneVolumeChange: (value: number) => void;
  className?: string;
}) {
  return (
    <div
      className={`grid gap-3 md:grid-cols-2 ${className ?? ""}`.trim()}
      data-testid="robot-workbench-audio-controls"
    >
      <div>
        <div className="mb-2 flex items-center gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">
            Speaker
          </p>
        </div>
        <WorkbenchAudioCard
          device={`${robotName} output`}
          platform={devicePlatform}
          value={speakerVolume}
          muted={speakerMuted}
          onToggleMuted={onToggleSpeakerMuted}
          onValueChange={onSpeakerVolumeChange}
          activeIcon={Volume2}
          mutedIcon={VolumeX}
          testId="robot-workbench-speaker-card"
        />
      </div>

      <div>
        <div className="mb-2 flex items-center gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">
            Microphone
          </p>
        </div>
        <WorkbenchAudioCard
          device={`${robotName} input`}
          platform={devicePlatform}
          value={microphoneVolume}
          muted={microphoneMuted}
          onToggleMuted={onToggleMicrophoneMuted}
          onValueChange={onMicrophoneVolumeChange}
          activeIcon={Mic}
          mutedIcon={MicOff}
          testId="robot-workbench-microphone-card"
        />
      </div>
    </div>
  );
}

export function MujocoPanel({
  projectPath,
  statusResult,
  layout = "compact",
}: {
  projectPath: string;
  statusResult?: ReachyStatusResult;
  layout?: "compact" | "expanded";
}) {
  const { settings } = useSettings();
  const robotSettings = {
    ...getDefaultRobotWorkbenchSettings(),
    ...(settings.robot_settings || {}),
  };
  const liveStatus = useReachyStatus(robotSettings);
  const reachyStatus = statusResult ?? liveStatus;
  const { connectionState, daemonBaseUrl, daemonStatus, error, lastUpdatedAt } =
    useMujocoStatus(robotSettings);
  const simulationEnabled = Boolean(daemonStatus?.simulation_enabled);
  const mockupEnabled = Boolean(daemonStatus?.mockup_sim_enabled);
  const backendLabel = getMujocoBackendLabel(daemonStatus);
  const {
    status: desktopDaemonStatus,
    error: desktopDaemonError,
    refresh: refreshDesktopDaemon,
    start: startDesktopDaemon,
    stop: stopDesktopDaemon,
    isStarting,
    isStopping,
    isBusy,
  } = useRobotDaemonProcess(projectPath);
  const panelStatus =
    connectionState === "disabled"
      ? "Disabled"
      : connectionState === "offline"
        ? "Offline"
        : connectionState === "connecting" && !daemonStatus
          ? "Connecting"
          : simulationEnabled || mockupEnabled
            ? "Live"
            : "Idle";
  const controlMode = daemonStatus?.backend_status?.motor_control_mode ?? "—";
  const mediaValue = daemonStatus
    ? daemonStatus.no_media
      ? "Headless"
      : daemonStatus.media_released
        ? "Released"
        : "Attached"
    : "—";
  const desktopProcessValue = formatProcessLifecycle(
    desktopDaemonStatus.lifecycle,
  );
  const desktopStartedAt = formatTimestamp(desktopDaemonStatus.started_at);
  const desktopCommand =
    desktopDaemonStatus.command || "reachy-mini-daemon --sim";
  const desktopWorkingDir = desktopDaemonStatus.working_dir || projectPath;
  const desktopLogs = desktopDaemonStatus.recent_logs || [];
  const runtimeRunning =
    desktopDaemonStatus.lifecycle === "running" ||
    simulationEnabled ||
    mockupEnabled;
  const viewportStatus =
    reachyStatus.connectionState === "live"
      ? "Live Pose"
      : runtimeRunning
        ? "Waiting State"
        : "Ready";
  const launchStatus =
    desktopDaemonStatus.lifecycle === "running"
      ? "Live"
      : isStarting
        ? "Starting"
        : isStopping
          ? "Stopping"
          : "Idle";
  const primaryLayoutClassName =
    layout === "expanded"
      ? "grid gap-4 xl:grid-cols-[minmax(300px,0.84fr)_minmax(340px,1.16fr)] xl:items-start"
      : "flex flex-col gap-3";
  const actionLayoutClassName =
    layout === "expanded" ? "grid gap-2 sm:grid-cols-3" : "flex flex-wrap gap-2";
  const startActionLabel =
    layout === "expanded"
      ? isStarting
        ? "Starting..."
        : "Start"
      : isStarting
        ? "Starting..."
        : "Start Simulation";
  const stopActionLabel =
    layout === "expanded"
      ? isStopping
        ? "Stopping..."
        : "Stop"
      : isStopping
        ? "Stopping..."
        : "Stop Runtime";
  const viewportMetricValue = layout === "expanded" ? "3D" : "Embedded 3D";
  const expandedToolbarClassName =
    "flex flex-wrap items-center gap-4 border-b border-border/60 px-4 pb-4 lg:flex-nowrap";
  const expandedPrimaryButtonClassName =
    "h-14 min-w-[164px] justify-start rounded-2xl px-6 text-base font-semibold";
  const expandedSecondaryButtonClassName =
    "h-14 min-w-[164px] justify-start rounded-2xl px-6 text-base font-semibold";
  const expandedGhostButtonClassName =
    "h-14 px-0 text-base font-semibold text-foreground hover:bg-transparent hover:text-foreground";

  return (
    <RobotPanelCard
      testId="mujoco-panel"
      eyebrow="Simulation"
      title="MuJoCo"
      status={panelStatus}
      statusTone={panelStatus === "Live" ? "success" : "neutral"}
    >
      <div className="flex flex-col gap-3">
        {layout === "expanded" ? (
          <div
            className={expandedToolbarClassName}
            data-testid="mujoco-expanded-toolbar"
          >
            <Button
              size="lg"
              className={expandedPrimaryButtonClassName}
              onClick={() => void startDesktopDaemon()}
              disabled={isBusy || desktopDaemonStatus.lifecycle === "running"}
            >
              <Play className="size-5" />
              {startActionLabel}
            </Button>
            <Button
              size="lg"
              variant="outline"
              className={expandedSecondaryButtonClassName}
              onClick={() => void stopDesktopDaemon()}
              disabled={isBusy || desktopDaemonStatus.lifecycle !== "running"}
            >
              <Square className="size-5" />
              {stopActionLabel}
            </Button>
            <Button
              size="lg"
              variant="ghost"
              className={expandedGhostButtonClassName}
              onClick={() => void refreshDesktopDaemon()}
              disabled={isBusy}
            >
              <RefreshCw className="size-5" />
              Refresh
            </Button>
          </div>
        ) : null}
        <div
          className={primaryLayoutClassName}
          data-testid="mujoco-primary-layout"
        >
          <div className="overflow-hidden rounded-2xl border border-border/60 bg-background/90">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/60 px-4 py-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Desktop Runtime
                </p>
              </div>
              <span className="inline-flex items-center rounded-full border border-border/70 bg-muted/25 px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                {launchStatus}
              </span>
            </div>
            <div className="space-y-3 px-4 py-4">
              {layout === "expanded" ? null : (
                <div className={actionLayoutClassName}>
                  <Button
                    size="sm"
                    onClick={() => void startDesktopDaemon()}
                    disabled={isBusy || desktopDaemonStatus.lifecycle === "running"}
                  >
                    <Play className="size-4" />
                    {startActionLabel}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void stopDesktopDaemon()}
                    disabled={isBusy || desktopDaemonStatus.lifecycle !== "running"}
                  >
                    <Square className="size-4" />
                    {stopActionLabel}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void refreshDesktopDaemon()}
                    disabled={isBusy}
                  >
                    <RefreshCw className="size-4" />
                    Refresh
                  </Button>
                </div>
              )}
              <div className="grid gap-2 md:grid-cols-2">
                <MetricRow
                  icon={Cpu}
                  label="Desktop Process"
                  value={desktopProcessValue}
                />
                <MetricRow
                  icon={Sparkles}
                  label="PID"
                  value={
                    desktopDaemonStatus.pid
                      ? String(desktopDaemonStatus.pid)
                      : "—"
                  }
                />
                <MetricRow
                  icon={Boxes}
                  label="Backend"
                  value={
                    connectionState === "disabled" ? "Disabled" : backendLabel
                  }
                />
                <MetricRow
                  icon={Orbit}
                  label="Viewport"
                  value={viewportMetricValue}
                />
                <MetricRow
                  icon={Cpu}
                  label="Daemon State"
                  value={formatDaemonLabel(daemonStatus?.state)}
                />
                <MetricRow
                  icon={Sparkles}
                  label="Control Mode"
                  value={controlMode}
                />
              </div>
              <div className="rounded-2xl border border-border/60 bg-muted/20 p-3 text-xs">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-foreground">Command</span>
                  <span className="max-w-[220px] truncate text-muted-foreground">
                    {desktopCommand}
                  </span>
                </div>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <span className="text-foreground">Working dir</span>
                  <span className="max-w-[220px] truncate text-muted-foreground">
                    {desktopWorkingDir}
                  </span>
                </div>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <span className="text-foreground">Started</span>
                  <span className="text-muted-foreground">
                    {desktopStartedAt}
                  </span>
                </div>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <span className="text-foreground">Last update</span>
                  <span className="text-muted-foreground">
                    {formatTimestamp(lastUpdatedAt)}
                  </span>
                </div>
              </div>
              {desktopLogs.length > 0 ? (
                <div className="rounded-2xl border border-border/60 bg-background/70 p-3">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                    Desktop Logs
                  </p>
                  <pre className="mt-3 max-h-36 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-muted-foreground">
                    {desktopLogs.slice(-8).join("\n")}
                  </pre>
                </div>
              ) : null}
            </div>
          </div>
          <div className="overflow-hidden rounded-2xl border border-border/60 bg-background/90">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/60 px-4 py-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Embedded 3D
                </p>
              </div>
              <span className="inline-flex items-center rounded-full border border-border/70 bg-muted/25 px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                {viewportStatus}
              </span>
            </div>
            <div className="space-y-3 px-4 py-4">
              <ReachySimulationViewport
                snapshot={reachyStatus.snapshot}
                connectionState={reachyStatus.connectionState}
                runtimeRunning={runtimeRunning}
              />
              {runtimeRunning && reachyStatus.connectionState !== "live" ? (
                <div className="rounded-2xl border border-border/60 bg-muted/20 px-3 py-2.5 text-xs text-muted-foreground">
                  {reachyStatus.error ||
                    "运行中，等待 Reachy 状态 websocket 的第一帧数据。"}
                </div>
              ) : null}
            </div>
          </div>
        </div>
        <div className="rounded-2xl border border-border/60 bg-background/70 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Runtime Details
          </p>
          <div className="mt-3 space-y-2 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Simulation</span>
              <span className="text-muted-foreground">
                {simulationEnabled
                  ? "active"
                  : mockupEnabled
                    ? "mockup"
                    : "disabled"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Media</span>
              <span className="text-muted-foreground">{mediaValue}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Version</span>
              <span className="text-muted-foreground">
                {daemonStatus?.version ?? "—"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Endpoint</span>
              <span className="max-w-[220px] truncate text-muted-foreground">
                {connectionState === "disabled" ? "Disabled" : daemonBaseUrl}
              </span>
            </div>
          </div>
        </div>
        {error ? (
          <div className="rounded-2xl border border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 px-3 py-2.5 text-xs text-[hsl(var(--warning))]">
            {error}
          </div>
        ) : null}
        {desktopDaemonError || desktopDaemonStatus.last_error ? (
          <div className="rounded-2xl border border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 px-3 py-2.5 text-xs text-[hsl(var(--warning))]">
            {desktopDaemonError || desktopDaemonStatus.last_error}
          </div>
        ) : null}
      </div>
    </RobotPanelCard>
  );
}

export function ReachyStatusPanel({
  statusResult,
}: {
  statusResult?: ReachyStatusResult;
} = {}) {
  const { settings } = useSettings();
  const robotSettings = {
    ...getDefaultRobotWorkbenchSettings(),
    ...(settings.robot_settings || {}),
  };
  const liveStatus = useReachyStatus(robotSettings);
  const { connectionState, snapshot, daemonBaseUrl, error, lastUpdatedAt } =
    statusResult ?? liveStatus;
  const headPose = getEulerPose(snapshot?.head_pose);

  const panelStatus =
    connectionState === "disabled"
      ? "Disabled"
      : connectionState === "live"
        ? "Live"
        : connectionState === "connecting"
          ? "Connecting"
          : "Offline";
  const robotLinkValue =
    connectionState === "disabled"
      ? "Disabled"
      : connectionState === "live"
        ? "Connected"
        : connectionState === "connecting"
          ? "Connecting"
          : "Disconnected";
  const streamValue =
    connectionState === "live"
      ? "Streaming"
      : connectionState === "connecting"
        ? "Opening"
        : connectionState === "disabled"
          ? "Off"
          : "Idle";
  const poseSummary = headPose
    ? `Yaw ${formatRadiansAsDegrees(headPose.yaw)} | Pitch ${formatRadiansAsDegrees(headPose.pitch)} | Roll ${formatRadiansAsDegrees(headPose.roll)}`
    : "Waiting for the first Reachy state frame";

  return (
    <RobotPanelCard
      testId="reachy-status-panel"
      eyebrow="Robot"
      title="Reachy Status"
      status={panelStatus}
      statusTone={connectionState === "live" ? "success" : "neutral"}
    >
      <div className="flex flex-col gap-3">
        <div className="grid gap-2 md:grid-cols-2">
          <MetricRow icon={Bot} label="Robot Link" value={robotLinkValue} />
          <MetricRow
            icon={RadioTower}
            label="State Stream"
            value={streamValue}
          />
          <MetricRow
            icon={Cpu}
            label="Control Mode"
            value={snapshot?.control_mode ?? "—"}
          />
          <MetricRow
            icon={Sparkles}
            label="Head Yaw"
            value={formatRadiansAsDegrees(headPose?.yaw)}
          />
        </div>
        <div className="rounded-2xl border border-border/60 bg-background/70 p-3">
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Pose</span>
              <span className="max-w-[220px] truncate text-muted-foreground">
                {poseSummary}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Body yaw</span>
              <span className="text-muted-foreground">
                {formatRadiansAsDegrees(snapshot?.body_yaw)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Antennas</span>
              <span className="text-muted-foreground">
                {formatAntennaPair(snapshot?.antennas_position)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Daemon</span>
              <span className="max-w-[220px] truncate text-muted-foreground">
                {daemonBaseUrl}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Last update</span>
              <span className="text-muted-foreground">
                {formatTimestamp(lastUpdatedAt)}
              </span>
            </div>
          </div>
        </div>
        {connectionState === "disabled" ? (
          <div className="rounded-2xl border border-border/60 bg-muted/20 px-3 py-2.5 text-xs text-muted-foreground">
            Live status is disabled
          </div>
        ) : null}
        {error ? (
          <div className="rounded-2xl border border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 px-3 py-2.5 text-xs text-[hsl(var(--warning))]">
            {error}
          </div>
        ) : null}
      </div>
    </RobotPanelCard>
  );
}

export function ReachyControllerPanel({
  statusResult,
}: {
  statusResult?: ReachyStatusResult;
} = {}) {
  const { settings } = useSettings();
  const robotSettings = {
    ...getDefaultRobotWorkbenchSettings(),
    ...(settings.robot_settings || {}),
  };
  const liveStatus = useReachyStatus(robotSettings);
  const { connectionState, snapshot, daemonBaseUrl } =
    statusResult ?? liveStatus;

  const panelStatus =
    connectionState === "live"
      ? "Ready"
      : connectionState === "connecting"
        ? "Connecting"
        : connectionState === "offline"
          ? "Offline"
          : "Blind";

  return (
    <RobotPanelCard
      testId="reachy-controller-panel"
      eyebrow="Control"
      title="Reachy Controller"
      status={panelStatus}
      statusTone={connectionState === "live" ? "success" : "neutral"}
    >
      <ReachyController
        daemonBaseUrl={daemonBaseUrl}
        snapshot={snapshot}
        syncState={connectionState}
      />
    </RobotPanelCard>
  );
}

function RobotWorkbenchImmersiveStage({
  reachyStatus,
  daemonStatus,
  daemonConnectionState,
  daemonError,
  daemonLastUpdatedAt,
  desktopDaemonStatus,
  desktopDaemonError,
  startDesktopDaemon,
  stopDesktopDaemon,
  refreshDesktopDaemon,
  isStarting,
  isStopping,
  isBusy,
}: {
  reachyStatus: ReachyStatusResult;
  daemonStatus: ReachyDaemonStatus | null;
  daemonConnectionState: ReachyStatusResult["connectionState"];
  daemonError: string | null;
  daemonLastUpdatedAt: string | null;
  desktopDaemonStatus: RobotDaemonProcessStatus;
  desktopDaemonError: string | null;
  startDesktopDaemon: () => Promise<unknown>;
  stopDesktopDaemon: () => Promise<unknown>;
  refreshDesktopDaemon: () => Promise<unknown>;
  isStarting: boolean;
  isStopping: boolean;
  isBusy: boolean;
}) {
  const backendLabel =
    daemonConnectionState === "disabled"
      ? "Disabled"
      : getMujocoBackendLabel(daemonStatus);
  const runtimeRunning =
    desktopDaemonStatus.lifecycle === "running" ||
    Boolean(daemonStatus?.simulation_enabled) ||
    Boolean(daemonStatus?.mockup_sim_enabled);
  const logs = desktopDaemonStatus.recent_logs.slice(-10);
  const robotName = getWorkbenchRobotName(daemonStatus);
  const modeBadge = getWorkbenchModeBadge(daemonStatus);
  const cameraUnavailableReason = daemonStatus?.no_media
    ? "Media disabled"
    : daemonStatus?.media_released
      ? "Media released"
      : !runtimeRunning
        ? "Start runtime"
        : daemonStatus &&
            !daemonStatus.camera_specs_name &&
            !daemonStatus.simulation_enabled &&
            !daemonStatus.mockup_sim_enabled
          ? "Camera unavailable"
          : null;
  const appVersionLabel = `App v${packageJson.version}`;
  const daemonVersionLabel = daemonStatus?.version
    ? `Daemon v${daemonStatus.version}`
    : "Daemon ?";

  return (
    <div
      className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden border-r border-slate-200/80 bg-[rgba(245,245,247,0.72)] px-3 pb-4 pt-[33px] shadow-[2px_0_8px_-2px_rgba(0,0,0,0.08)]"
      data-testid="robot-workbench-stage-column"
    >
      <div className="w-full flex-none">
        <div
          className="mb-3 flex flex-wrap items-center gap-3 border-b border-slate-200/70 pb-3"
          data-testid="robot-workbench-stage-toolbar"
        >
          <Button
            className="!h-12 !min-w-[148px] justify-start rounded-xl px-5 text-sm font-semibold"
            onClick={() => void startDesktopDaemon()}
            disabled={isBusy || desktopDaemonStatus.lifecycle === "running"}
          >
            <Play className="size-4" />
            {isStarting ? "Starting..." : "Start Simulation"}
          </Button>
          <Button
            variant="outline"
            className="!h-12 !min-w-[148px] justify-start rounded-xl border-slate-200 bg-white/80 px-5 text-sm font-semibold text-slate-900"
            onClick={() => void stopDesktopDaemon()}
            disabled={isBusy || desktopDaemonStatus.lifecycle !== "running"}
          >
            <Square className="size-4" />
            {isStopping ? "Stopping..." : "Stop Runtime"}
          </Button>
          <Button
            variant="ghost"
            className="!h-12 px-0 text-sm font-semibold text-slate-950 hover:bg-transparent"
            onClick={() => void refreshDesktopDaemon()}
            disabled={isBusy}
          >
            <RefreshCw className="size-4" />
            Refresh
          </Button>
        </div>

        <div className="relative mb-1 overflow-visible" data-testid="robot-workbench-stage-hero">
          <ReachySimulationViewport
            snapshot={reachyStatus.snapshot}
            connectionState={reachyStatus.connectionState}
            runtimeRunning={runtimeRunning}
            size="immersive"
          />

          <div
            className="absolute -bottom-[60px] right-5 h-[105px] w-[140px] overflow-hidden rounded-[12px] bg-slate-950 shadow-[0_18px_40px_rgba(15,23,42,0.2)]"
            data-testid="robot-workbench-camera-overlay"
          >
            <WorkbenchCameraFeed
              daemonBaseUrl={reachyStatus.daemonBaseUrl}
              enabled={runtimeRunning}
              unavailableReason={cameraUnavailableReason}
            />
          </div>
        </div>

        <div
          className="grid gap-x-5 gap-y-3 py-2 lg:grid-cols-[minmax(0,1fr)_140px] lg:items-start"
          data-testid="robot-workbench-stage-header"
        >
          <div
            className="min-w-0"
            data-testid="robot-workbench-stage-title-block"
          >
            <div className="flex items-center gap-2">
              <h2
                className="truncate whitespace-nowrap text-[20px] font-semibold tracking-[-0.03em] text-slate-950"
                data-testid="robot-workbench-stage-title"
              >
                {robotName}
              </h2>
              <span className="inline-flex items-center rounded-[4px] bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-700">
                {modeBadge}
              </span>
            </div>
            <p
              className="mt-1 font-mono text-[9px] text-slate-400"
              data-testid="robot-workbench-stage-version-line"
            >
              {appVersionLabel} • {daemonVersionLabel}
            </p>
          </div>
          <div
            aria-hidden="true"
            className="hidden h-[72px] lg:block"
            data-testid="robot-workbench-stage-camera-slot"
          />
        </div>

        <div className="mb-4 text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-500">
          <span className="sr-only">Backend</span>
          <span aria-hidden="true">{backendLabel}</span>
          <span aria-hidden="true" className="mx-2 text-slate-300">
            /
          </span>
          <span aria-hidden="true">{formatTimestamp(daemonLastUpdatedAt)}</span>
        </div>
      </div>

      <div
        className="mt-1 flex flex-none flex-col"
        data-testid="robot-workbench-stage-logs"
      >
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">
            Logs
          </p>
        </div>
        <div
          className="h-[clamp(108px,18vh,140px)] overflow-auto rounded-[12px] bg-slate-950 px-3 py-2"
          data-testid="robot-workbench-stage-logs-scroll"
        >
          {logs.length > 0 ? (
            <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-emerald-300">
              {logs.join("\n")}
            </pre>
          ) : (
            <div className="flex h-full items-center">
              <p className="font-mono text-[11px] leading-5 text-slate-400">
                Waiting for desktop daemon logs...
              </p>
            </div>
          )}
        </div>
      </div>

      {daemonError || desktopDaemonError || desktopDaemonStatus.last_error ? (
        <div className="mt-3 rounded-[14px] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
          {daemonError || desktopDaemonError || desktopDaemonStatus.last_error}
        </div>
      ) : null}
    </div>
  );
}

function RobotWorkbenchControllerSurface({
  reachyStatus,
  robotName,
  devicePlatform,
  speakerVolume,
  microphoneVolume,
  speakerMuted,
  microphoneMuted,
  onToggleSpeakerMuted,
  onToggleMicrophoneMuted,
  onSpeakerVolumeChange,
  onMicrophoneVolumeChange,
}: {
  reachyStatus: ReachyStatusResult;
  robotName: string;
  devicePlatform: string;
  speakerVolume: number;
  microphoneVolume: number;
  speakerMuted: boolean;
  microphoneMuted: boolean;
  onToggleSpeakerMuted: () => void;
  onToggleMicrophoneMuted: () => void;
  onSpeakerVolumeChange: (value: number) => void;
  onMicrophoneVolumeChange: (value: number) => void;
}) {
  return (
    <div
      className="flex h-full min-h-0 min-w-0 flex-col bg-transparent pt-[33px] -translate-y-2"
      data-testid="robot-workbench-controller-column"
    >
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="px-2 pt-1.5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-[20px] font-semibold tracking-[-0.03em] text-slate-950">
              Controller
            </h2>
            <div className="flex size-[30px] items-center justify-center rounded-full border border-slate-200 bg-white/80 text-amber-500">
              <Gamepad2 className="size-4" />
            </div>
          </div>
        </div>
        <div
          className="min-h-0 flex-1 overflow-auto px-3 pb-3 pt-3"
          data-testid="robot-workbench-controller-scroll"
          style={workbenchPanelVars}
        >
          <ReachyController
            daemonBaseUrl={reachyStatus.daemonBaseUrl}
            snapshot={reachyStatus.snapshot}
            syncState={reachyStatus.connectionState}
            showOverviewMetrics={false}
            showResetAction={false}
            showStatusMessages={false}
            density="compact"
          />
          <WorkbenchAudioControls
            robotName={robotName}
            devicePlatform={devicePlatform}
            speakerVolume={speakerVolume}
            microphoneVolume={microphoneVolume}
            speakerMuted={speakerMuted}
            microphoneMuted={microphoneMuted}
            onToggleSpeakerMuted={onToggleSpeakerMuted}
            onToggleMicrophoneMuted={onToggleMicrophoneMuted}
            onSpeakerVolumeChange={onSpeakerVolumeChange}
            onMicrophoneVolumeChange={onMicrophoneVolumeChange}
            className="mt-3"
          />
        </div>
      </div>
    </div>
  );
}

export function RobotSidePanel({
  projectName,
  projectPath,
  width = DEFAULT_ROBOT_PANEL_WIDTH,
}: {
  projectName: string;
  projectPath: string;
  width?: number;
}) {
  const { settings } = useSettings();
  const robotSettings = {
    ...getDefaultRobotWorkbenchSettings(),
    ...(settings.robot_settings || {}),
  };
  const reachyStatus = useReachyStatus(robotSettings);

  return (
    <aside
      className="flex min-h-0 shrink-0 flex-col overflow-hidden border-l border-border/70 bg-muted/18"
      data-testid="robot-side-panel"
      style={{
        width: `${width}px`,
        minWidth: `${width}px`,
        maxWidth: `${width}px`,
        flexBasis: `${width}px`,
      }}
    >
      <div className="shrink-0 border-b border-border/70 px-4 py-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          Robot Workbench
        </p>
        <div className="mt-1 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">
              {projectName}
            </p>
          </div>
          <span className="inline-flex items-center rounded-full border border-border/70 bg-background px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
            Shell
          </span>
        </div>
      </div>
      <ScrollArea
        className="min-h-0 flex-1"
        data-testid="robot-side-panel-scroll"
      >
        <div className="flex flex-col gap-3 p-3">
          <MujocoPanel projectPath={projectPath} statusResult={reachyStatus} />
          <ReachyStatusPanel statusResult={reachyStatus} />
          <ReachyControllerPanel statusResult={reachyStatus} />
        </div>
      </ScrollArea>
    </aside>
  );
}

export function RobotWorkbenchMainPanel({
  projectPath,
}: {
  projectPath: string;
}) {
  const [speakerVolume, setSpeakerVolume] = useState(82);
  const [microphoneVolume, setMicrophoneVolume] = useState(74);
  const [speakerMuted, setSpeakerMuted] = useState(false);
  const [microphoneMuted, setMicrophoneMuted] = useState(false);
  const { settings } = useSettings();
  const robotSettings = {
    ...getDefaultRobotWorkbenchSettings(),
    ...(settings.robot_settings || {}),
  };
  const reachyStatus = useReachyStatus(robotSettings);
  const mujocoStatus = useMujocoStatus(robotSettings);
  const {
    status: desktopDaemonStatus,
    error: desktopDaemonError,
    refresh: refreshDesktopDaemon,
    start: startDesktopDaemon,
    stop: stopDesktopDaemon,
    isStarting,
    isStopping,
    isBusy,
  } = useRobotDaemonProcess(projectPath);
  const workbenchRobotName = getWorkbenchRobotName(mujocoStatus.daemonStatus);
  const devicePlatform = mujocoStatus.daemonStatus?.wireless_version
    ? "Network"
    : mujocoStatus.daemonStatus?.simulation_enabled
      ? "Simulation"
      : "Robot";

  return (
    <section
      className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-[linear-gradient(180deg,#f8fafc_0%,#eef2f7_100%)]"
      data-testid="robot-workbench-main-panel"
    >
      <div
        className="min-h-0 flex-1 overflow-hidden"
        data-testid="robot-workbench-main-scroll"
      >
        <div
          className="h-full"
          data-testid="robot-workbench-immersive-shell"
          style={workbenchShellStyle}
        >
          <div
            className="grid h-full min-h-0 gap-0 xl:grid-cols-[minmax(420px,520px)_minmax(480px,1fr)]"
            data-testid="robot-workbench-main-layout"
          >
            <RobotWorkbenchImmersiveStage
              reachyStatus={reachyStatus}
              daemonStatus={mujocoStatus.daemonStatus}
              daemonConnectionState={mujocoStatus.connectionState}
              daemonError={mujocoStatus.error}
              daemonLastUpdatedAt={mujocoStatus.lastUpdatedAt}
              desktopDaemonStatus={desktopDaemonStatus}
              desktopDaemonError={desktopDaemonError}
              startDesktopDaemon={startDesktopDaemon}
              stopDesktopDaemon={stopDesktopDaemon}
              refreshDesktopDaemon={refreshDesktopDaemon}
              isStarting={isStarting}
              isStopping={isStopping}
              isBusy={isBusy}
            />
            <RobotWorkbenchControllerSurface
              reachyStatus={reachyStatus}
              robotName={workbenchRobotName}
              devicePlatform={devicePlatform}
              speakerVolume={speakerVolume}
              microphoneVolume={microphoneVolume}
              speakerMuted={speakerMuted}
              microphoneMuted={microphoneMuted}
              onToggleSpeakerMuted={() => setSpeakerMuted((current) => !current)}
              onToggleMicrophoneMuted={() =>
                setMicrophoneMuted((current) => !current)
              }
              onSpeakerVolumeChange={setSpeakerVolume}
              onMicrophoneVolumeChange={setMicrophoneVolume}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function RobotSidePanelRail({
  projectName,
  onToggle,
}: {
  projectName: string;
  onToggle: () => void;
}) {
  return (
    <aside
      className="relative flex w-11 shrink-0 flex-col items-center overflow-visible border-l border-border/70 bg-muted/18"
      data-testid="robot-side-panel-collapsed"
    >
      <button
        type="button"
        className={dockToggleButtonClassName}
        onClick={onToggle}
        aria-label="Expand robot workbench panel"
        data-testid="robot-side-panel-expand"
        title={`Expand ${projectName} robot workbench`}
      >
        <ChevronLeft className="size-4" />
      </button>
      <div className="flex min-h-0 flex-1 items-end justify-center py-4">
        <span className="[writing-mode:vertical-rl] rotate-180 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          Robot
        </span>
      </div>
    </aside>
  );
}

export function RobotWorkbenchDock({
  projectName,
  projectPath,
  collapsed,
  onToggle,
}: {
  projectName: string;
  projectPath: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const [panelWidth, setPanelWidth] = useState(getInitialRobotPanelWidth);
  const [isResizing, setIsResizing] = useState(false);
  const dragStartXRef = useRef(0);
  const dragStartWidthRef = useRef(panelWidth);

  const handleResizeMouseDown = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();

      dragStartXRef.current = event.clientX;
      dragStartWidthRef.current = panelWidth;
      setIsResizing(true);

      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [panelWidth],
  );

  useEffect(() => {
    if (!isResizing) {
      return;
    }

    const handleMouseMove = (event: MouseEvent) => {
      const deltaX = dragStartXRef.current - event.clientX;
      setPanelWidth(clampRobotPanelWidth(dragStartWidthRef.current + deltaX));
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [isResizing]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(
      ROBOT_PANEL_WIDTH_STORAGE_KEY,
      String(panelWidth),
    );
  }, [panelWidth]);

  if (collapsed) {
    return <RobotSidePanelRail projectName={projectName} onToggle={onToggle} />;
  }

  return (
    <div
      className="relative flex shrink-0 overflow-visible"
      style={{ width: `${panelWidth}px`, flexBasis: `${panelWidth}px` }}
      data-testid="robot-workbench-dock"
    >
      <div
        className={`absolute left-0 top-0 z-20 h-full w-3 -translate-x-1/2 cursor-col-resize ${
          isResizing ? "bg-border/70" : "bg-transparent hover:bg-border/45"
        } transition-colors`}
        onMouseDown={handleResizeMouseDown}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize robot workbench panel"
        data-testid="robot-side-panel-resize-handle"
      >
        <div className="absolute left-1/2 top-1/2 h-16 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-border/60" />
      </div>
      <button
        type="button"
        className={dockToggleButtonClassName}
        onClick={onToggle}
        aria-label="Collapse robot workbench panel"
        data-testid="robot-side-panel-collapse"
        title={`Collapse ${projectName} robot workbench`}
      >
        <ChevronRight className="size-4" />
      </button>
      <RobotSidePanel
        projectName={projectName}
        projectPath={projectPath}
        width={panelWidth}
      />
    </div>
  );
}
