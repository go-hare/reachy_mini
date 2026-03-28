import {
  Bot,
  Boxes,
  ChevronLeft,
  ChevronRight,
  Cpu,
  ExternalLink,
  Orbit,
  Play,
  RadioTower,
  RefreshCw,
  Sparkles,
  Square,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useSettings } from "@/contexts/settings-context";
import ReachyController from "@/components/controller/ReachyController";
import { useMujocoStatus } from "@/hooks/use-mujoco-status";
import { useRobotDaemonProcess } from "@/hooks/use-robot-daemon-process";
import { useRobotViewerProcess } from "@/hooks/use-robot-viewer-process";
import {
  useReachyStatus,
  type ReachyStatusResult,
} from "@/hooks/use-reachy-status";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
  DEFAULT_MUJOCO_WEB_VIEWER_URL,
  getDefaultRobotWorkbenchSettings,
  isWorkbenchLaunchCommandTemplate,
  normalizeWorkbenchLaunchCommand,
  normalizeWorkbenchViewerUrl,
  probeMujocoViewerUrl,
  type ReachyDaemonStatus,
  type MujocoViewerProbeResult,
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
            className={`inline-flex min-w-[88px] shrink-0 items-center justify-center rounded-full border px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] ${statusClassName}`}
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
      <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
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

function getMujocoViewerModeLabel(viewerUrl: string) {
  return viewerUrl ? "Web Viewer" : "Native Window";
}

function getViewerServiceCommandLabel(
  statusCommand?: string | null,
  launchCommand?: string,
) {
  return statusCommand?.trim() || launchCommand?.trim() || "Not configured";
}

type ViewerAvailability = "idle" | "checking" | "ready" | "offline";

function getViewerSurfaceStatusLabel(
  viewerUrl: string,
  viewerAvailability: ViewerAvailability,
  viewerFrameStatus: "idle" | "loading" | "ready",
) {
  if (!viewerUrl) return "Awaiting URL";
  if (viewerAvailability === "checking") return "Checking Viewer";
  if (viewerAvailability === "offline") return "Viewer Offline";
  if (viewerFrameStatus === "loading") return "Embedded Loading";
  if (viewerFrameStatus === "ready") return "Embedded Ready";
  return "Awaiting Viewer";
}

function getViewerProbeHint(probeResult?: MujocoViewerProbeResult | null) {
  if (!probeResult) return null;
  if (probeResult.error?.trim()) return probeResult.error.trim();
  if (typeof probeResult.status === "number") {
    return `HTTP ${probeResult.status}`;
  }
  return null;
}

export function MujocoPanel({ projectPath }: { projectPath: string }) {
  const { settings, updateSettings } = useSettings();
  const robotSettings = {
    ...getDefaultRobotWorkbenchSettings(),
    ...(settings.robot_settings || {}),
  };
  const { connectionState, daemonBaseUrl, daemonStatus, error, lastUpdatedAt } =
    useMujocoStatus(robotSettings);
  const viewerUrl = normalizeWorkbenchViewerUrl(
    robotSettings.mujoco_viewer_url,
  );
  const viewerLaunchCommand = normalizeWorkbenchLaunchCommand(
    robotSettings.mujoco_viewer_launch_command,
  );
  const viewerLaunchCommandIsTemplate =
    isWorkbenchLaunchCommandTemplate(viewerLaunchCommand);
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
  const {
    status: viewerServiceStatus,
    error: viewerServiceError,
    refresh: refreshViewerService,
    start: startViewerService,
    stop: stopViewerService,
    isStarting: isStartingViewerService,
    isStopping: isStoppingViewerService,
    isBusy: isViewerServiceBusy,
  } = useRobotViewerProcess(projectPath, viewerLaunchCommand);
  const [viewerFrameKey, setViewerFrameKey] = useState(0);
  const [viewerFrameStatus, setViewerFrameStatus] = useState<
    "idle" | "loading" | "ready"
  >(viewerUrl ? "loading" : "idle");
  const [viewerAvailability, setViewerAvailability] =
    useState<ViewerAvailability>(viewerUrl ? "checking" : "idle");
  const [viewerProbeResult, setViewerProbeResult] =
    useState<MujocoViewerProbeResult | null>(null);
  const [viewerProbeNonce, setViewerProbeNonce] = useState(0);
  const [viewerSettingsAction, setViewerSettingsAction] = useState<
    "preset" | "clear" | null
  >(null);
  const [viewerError, setViewerError] = useState<string | null>(null);
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
  const launchStatus =
    desktopDaemonStatus.lifecycle === "running"
      ? "Desktop Runtime Live"
      : isStarting
        ? "Starting"
        : isStopping
          ? "Stopping"
          : "Desktop Runtime Idle";
  const viewerServiceProcessValue = formatProcessLifecycle(
    viewerServiceStatus.lifecycle,
  );
  const viewerServiceStartedAt = formatTimestamp(
    viewerServiceStatus.started_at,
  );
  const viewerServiceCommand = getViewerServiceCommandLabel(
    viewerServiceStatus.command,
    viewerLaunchCommand,
  );
  const viewerServiceWorkingDir =
    viewerServiceStatus.working_dir || projectPath;
  const viewerServiceLogs = viewerServiceStatus.recent_logs || [];
  const viewerServiceLaunchStatus =
    viewerServiceStatus.lifecycle === "running"
      ? "Viewer Service Live"
      : isStartingViewerService
        ? "Starting"
        : isStoppingViewerService
        ? "Stopping"
          : "Viewer Service Idle";
  const viewerSurfaceStatus = getViewerSurfaceStatusLabel(
    viewerUrl,
    viewerAvailability,
    viewerFrameStatus,
  );
  const viewerProbeHint = getViewerProbeHint(viewerProbeResult);

  useEffect(() => {
    setViewerAvailability(viewerUrl ? "checking" : "idle");
    setViewerProbeResult(null);
    setViewerFrameStatus(viewerUrl ? "loading" : "idle");
    setViewerError(null);
  }, [viewerUrl]);

  useEffect(() => {
    if (!viewerUrl || viewerServiceStatus.lifecycle !== "running") {
      return;
    }

    setViewerProbeNonce((current) => current + 1);
  }, [viewerServiceStatus.lifecycle, viewerUrl]);

  useEffect(() => {
    let cancelled = false;

    async function runViewerProbe() {
      if (!viewerUrl) {
        setViewerAvailability("idle");
        setViewerProbeResult(null);
        setViewerFrameStatus("idle");
        return;
      }

      setViewerAvailability("checking");
      setViewerProbeResult(null);
      setViewerFrameStatus("loading");

      try {
        const probeResult = await probeMujocoViewerUrl(viewerUrl);
        if (cancelled) return;

        setViewerProbeResult(probeResult);

        if (probeResult.ok) {
          setViewerAvailability("ready");
          setViewerFrameStatus("loading");
          return;
        }

        setViewerAvailability("offline");
        setViewerFrameStatus("idle");
      } catch (error) {
        if (cancelled) return;

        setViewerAvailability("offline");
        setViewerFrameStatus("idle");
        setViewerProbeResult({
          ok: false,
          status: null,
          error:
            error instanceof Error ? error.message : "Viewer probe failed",
        });
      }
    }

    void runViewerProbe();

    return () => {
      cancelled = true;
    };
  }, [viewerUrl, viewerProbeNonce]);

  const handleApplyViewerPreset = useCallback(async () => {
    setViewerSettingsAction("preset");
    setViewerError(null);

    try {
      await updateSettings({
        robot_settings: {
          ...robotSettings,
          mujoco_viewer_url: DEFAULT_MUJOCO_WEB_VIEWER_URL,
        },
      });
    } catch (error) {
      setViewerError(
        error instanceof Error
          ? error.message
          : "Failed to save MuJoCo Web Viewer preset",
      );
    } finally {
      setViewerSettingsAction(null);
    }
  }, [updateSettings]);

  const handleClearViewerUrl = useCallback(async () => {
    setViewerSettingsAction("clear");
    setViewerError(null);

    try {
      await updateSettings({
        robot_settings: {
          ...robotSettings,
          mujoco_viewer_url: "",
        },
      });
      setViewerFrameStatus("idle");
    } catch (error) {
      setViewerError(
        error instanceof Error
          ? error.message
          : "Failed to clear MuJoCo Web Viewer URL",
      );
    } finally {
      setViewerSettingsAction(null);
    }
  }, [updateSettings]);

  const handleOpenViewerInBrowser = useCallback(async () => {
    if (!viewerUrl) return;

    try {
      setViewerError(null);
      await openUrl(viewerUrl);
    } catch (error) {
      setViewerError(
        error instanceof Error
          ? error.message
          : "Failed to open MuJoCo Web Viewer in browser",
      );
    }
  }, [viewerUrl]);

  const handleReloadViewer = useCallback(() => {
    if (!viewerUrl) return;

    setViewerError(null);
    setViewerFrameKey((current) => current + 1);
    setViewerProbeNonce((current) => current + 1);
  }, [viewerUrl]);

  return (
    <RobotPanelCard
      testId="mujoco-panel"
      eyebrow="Simulation"
      title="MuJoCo"
      status={panelStatus}
      statusTone={panelStatus === "Live" ? "success" : "neutral"}
    >
      <div className="flex flex-col gap-3">
        <div className="overflow-hidden rounded-2xl border border-border/60 bg-background/90">
          <div className="flex items-center justify-between gap-3 border-b border-border/60 px-4 py-3">
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
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => void startDesktopDaemon()}
                disabled={isBusy || desktopDaemonStatus.lifecycle === "running"}
              >
                <Play className="size-4" />
                {isStarting ? "Starting..." : "Start Simulation"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void stopDesktopDaemon()}
                disabled={isBusy || desktopDaemonStatus.lifecycle !== "running"}
              >
                <Square className="size-4" />
                {isStopping ? "Stopping..." : "Stop Runtime"}
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
                label="Viewer"
                value={getMujocoViewerModeLabel(viewerUrl)}
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
          <div className="flex items-center justify-between gap-3 border-b border-border/60 px-4 py-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                Viewer Surface
              </p>
            </div>
            <span className="inline-flex items-center rounded-full border border-border/70 bg-muted/25 px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
              {getMujocoViewerModeLabel(viewerUrl)}
            </span>
          </div>
          <div className="space-y-3 border-b border-border/60 px-4 py-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Viewer Service
                </p>
              </div>
              <span className="inline-flex items-center rounded-full border border-border/70 bg-muted/25 px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
                {viewerServiceLaunchStatus}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => void startViewerService()}
                disabled={
                  isViewerServiceBusy ||
                  viewerServiceStatus.lifecycle === "running" ||
                  !viewerLaunchCommand ||
                  viewerLaunchCommandIsTemplate
                }
              >
                <Play className="size-4" />
                {isStartingViewerService ? "Starting..." : "Start Viewer"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void stopViewerService()}
                disabled={
                  isViewerServiceBusy ||
                  viewerServiceStatus.lifecycle !== "running"
                }
              >
                <Square className="size-4" />
                {isStoppingViewerService ? "Stopping..." : "Stop Viewer"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void refreshViewerService()}
                disabled={isViewerServiceBusy}
              >
                <RefreshCw className="size-4" />
                Refresh Service
              </Button>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <MetricRow
                icon={Cpu}
                label="Viewer Process"
                value={viewerServiceProcessValue}
              />
              <MetricRow
                icon={Sparkles}
                label="Viewer PID"
                value={
                  viewerServiceStatus.pid
                    ? String(viewerServiceStatus.pid)
                    : "—"
                }
              />
            </div>
            <div className="rounded-2xl border border-border/60 bg-muted/20 p-3 text-xs">
              <div className="flex items-center justify-between gap-3">
                <span className="text-foreground">Launch command</span>
                <span className="max-w-[220px] truncate text-muted-foreground">
                  {viewerServiceCommand}
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between gap-3">
                <span className="text-foreground">Working dir</span>
                <span className="max-w-[220px] truncate text-muted-foreground">
                  {viewerServiceWorkingDir}
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between gap-3">
                <span className="text-foreground">Started</span>
                <span className="text-muted-foreground">
                  {viewerServiceStartedAt}
                </span>
              </div>
            </div>
            {!viewerLaunchCommand ? (
              <div className="rounded-2xl border border-border/60 bg-muted/20 px-3 py-2.5 text-xs text-muted-foreground">
                先在 Settings 里填 `MuJoCo Web Viewer Launch
                Command`，这里才能一键拉起 viewer 服务。
              </div>
            ) : null}
            {viewerLaunchCommandIsTemplate ? (
              <div className="rounded-2xl border border-border/60 bg-muted/20 px-3 py-2.5 text-xs text-muted-foreground">
                当前是默认模板命令。先把 `your_web_viewer` 换成你真实的 Python
                viewer 入口，再点 `Start Viewer`。
              </div>
            ) : null}
            {viewerServiceLogs.length > 0 ? (
              <div className="rounded-2xl border border-border/60 bg-background/70 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Viewer Logs
                </p>
                <pre className="mt-3 max-h-36 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-muted-foreground">
                  {viewerServiceLogs.slice(-8).join("\n")}
                </pre>
              </div>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-3">
            {!viewerUrl ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => void handleApplyViewerPreset()}
                disabled={viewerSettingsAction !== null}
              >
                {viewerSettingsAction === "preset"
                  ? "Applying..."
                  : "Use Local Preset"}
              </Button>
            ) : (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void handleOpenViewerInBrowser()}
                >
                  <ExternalLink className="size-4" />
                  Open in Browser
                </Button>
                <Button size="sm" variant="ghost" onClick={handleReloadViewer}>
                  <RefreshCw className="size-4" />
                  Reload
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void handleClearViewerUrl()}
                  disabled={viewerSettingsAction !== null}
                >
                  {viewerSettingsAction === "clear"
                    ? "Clearing..."
                    : "Clear URL"}
                </Button>
              </>
            )}
            <span className="ml-auto text-[11px] font-medium text-muted-foreground">
              {viewerSurfaceStatus}
            </span>
          </div>
          <div className="relative min-h-[160px] bg-[radial-gradient(circle_at_top,_hsl(var(--muted))_0%,_transparent_75%)]">
            {!viewerUrl ? (
              <div className="flex h-[160px] items-center justify-center px-5 text-center">
                <div className="space-y-2">
                  <div className="mx-auto flex size-10 items-center justify-center rounded-2xl border border-border/70 bg-background">
                    <Orbit className="size-5 text-foreground" />
                  </div>
                  <p className="text-sm font-medium text-foreground">
                    MuJoCo 当前走原生窗口。
                  </p>
                  <p className="text-xs leading-5 text-muted-foreground">
                    右侧这里只是 Web Viewer 接入口。先点 `Use Local Preset`，
                    后面本地 viewer 服务起来后就会直接嵌到这里。
                  </p>
                </div>
              </div>
            ) : viewerAvailability === "checking" ? (
              <div className="flex h-[160px] items-center justify-center px-5 text-center">
                <div className="space-y-2">
                  <div className="mx-auto flex size-10 items-center justify-center rounded-2xl border border-border/70 bg-background">
                    <RefreshCw className="size-5 animate-spin text-foreground" />
                  </div>
                  <p className="text-sm font-medium text-foreground">
                    正在检查 MuJoCo Viewer
                  </p>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {viewerUrl}
                  </p>
                </div>
              </div>
            ) : viewerAvailability === "ready" ? (
              <>
                {viewerFrameStatus === "loading" ? (
                  <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-background/70 backdrop-blur-[1px]">
                    <div className="space-y-1 text-center">
                      <p className="text-sm font-medium text-foreground">
                        Loading MuJoCo Web Viewer
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {viewerUrl}
                      </p>
                    </div>
                  </div>
                ) : null}
                <iframe
                  key={`${viewerUrl}:${viewerFrameKey}`}
                  title="MuJoCo Viewer"
                  src={viewerUrl}
                  className="h-[160px] w-full border-0 bg-background"
                  loading="lazy"
                  referrerPolicy="no-referrer"
                  onLoad={() => setViewerFrameStatus("ready")}
                />
              </>
            ) : (
              <div className="flex h-[160px] items-center justify-center px-5 text-center">
                <div className="space-y-2">
                  <div className="mx-auto flex size-10 items-center justify-center rounded-2xl border border-border/70 bg-background">
                    <Orbit className="size-5 text-foreground" />
                  </div>
                  <p className="text-sm font-medium text-foreground">
                    {viewerServiceStatus.lifecycle === "running"
                      ? "Viewer 服务已启动，但页面还没就绪。"
                      : "Viewer 服务未启动或地址不可达。"}
                  </p>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {viewerProbeHint || viewerUrl}
                  </p>
                  <p className="text-xs leading-5 text-muted-foreground">
                    你这台机器当前没有这个服务时，这是正常状态。先配置好
                    Python viewer service，再点 `Start Viewer` 或 `Reload`。
                  </p>
                </div>
              </div>
            )}
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
        {viewerServiceError || viewerServiceStatus.last_error ? (
          <div className="rounded-2xl border border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 px-3 py-2.5 text-xs text-[hsl(var(--warning))]">
            {viewerServiceError || viewerServiceStatus.last_error}
          </div>
        ) : null}
        {viewerError ? (
          <div className="rounded-2xl border border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 px-3 py-2.5 text-xs text-[hsl(var(--warning))]">
            {viewerError}
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
          <MujocoPanel projectPath={projectPath} />
          <ReachyStatusPanel statusResult={reachyStatus} />
          <ReachyControllerPanel statusResult={reachyStatus} />
        </div>
      </ScrollArea>
    </aside>
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
