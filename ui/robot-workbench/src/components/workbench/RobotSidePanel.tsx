import { Bot, Boxes, Cpu, Orbit, RadioTower, Sparkles } from "lucide-react"
import type { ComponentType, ReactNode } from "react"
import { useSettings } from "@/contexts/settings-context"
import { useReachyStatus } from "@/hooks/use-reachy-status"
import {
  getDefaultRobotWorkbenchSettings,
  type ReachyXYZRPYPose,
} from "@/lib/reachy-daemon"

interface RobotPanelCardProps {
  title: string
  eyebrow: string
  description: string
  status: string
  statusTone?: "neutral" | "success"
  testId: string
  children: ReactNode
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
      : "border-border/70 bg-muted/45 text-muted-foreground"

  return (
    <section
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border/70 bg-background/95 shadow-sm"
      data-testid={testId}
    >
      <div className="border-b border-border/70 px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              {eyebrow}
            </p>
            <h2 className="mt-1 text-sm font-semibold text-foreground">{title}</h2>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
          </div>
          <span
            className={`inline-flex shrink-0 items-center rounded-full border px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] ${statusClassName}`}
          >
            {status}
          </span>
        </div>
      </div>
      <div className="min-h-0 flex-1 px-4 py-3">{children}</div>
    </section>
  )
}

function MetricRow({
  icon: Icon,
  label,
  value,
}: {
  icon: ComponentType<{ className?: string }>
  label: string
  value: string
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-muted/25 px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <Icon className="size-4 shrink-0 text-muted-foreground" />
        <span className="truncate text-xs font-medium text-foreground">{label}</span>
      </div>
      <span className="shrink-0 text-[11px] font-medium text-muted-foreground">{value}</span>
    </div>
  )
}

function formatRadiansAsDegrees(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) return "—"
  return `${((value * 180) / Math.PI).toFixed(1)}deg`
}

function getEulerPose(pose: unknown): ReachyXYZRPYPose | null {
  if (!pose || typeof pose !== "object" || Array.isArray(pose)) return null

  const maybePose = pose as Partial<ReachyXYZRPYPose>
  if (
    typeof maybePose.x === "number" &&
    typeof maybePose.y === "number" &&
    typeof maybePose.z === "number" &&
    typeof maybePose.roll === "number" &&
    typeof maybePose.pitch === "number" &&
    typeof maybePose.yaw === "number"
  ) {
    return maybePose as ReachyXYZRPYPose
  }

  return null
}

function formatAntennaPair(positions?: number[] | null) {
  if (!Array.isArray(positions) || positions.length < 2) return "—"
  return `${formatRadiansAsDegrees(positions[0])} / ${formatRadiansAsDegrees(positions[1])}`
}

function formatTimestamp(value?: string | null) {
  if (!value) return "—"

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value

  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
}

export function MujocoPanel() {
  return (
    <RobotPanelCard
      testId="mujoco-panel"
      eyebrow="Simulation"
      title="MuJoCo"
      description="预留仿真画布、场景状态和策略联动位置，先把桌面工作区插槽固定下来。"
      status="Pending"
    >
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div className="flex min-h-[160px] flex-1 items-center justify-center rounded-2xl border border-dashed border-border/70 bg-[radial-gradient(circle_at_top,_hsl(var(--muted))_0%,_transparent_60%)] px-5 text-center">
          <div className="space-y-2">
            <div className="mx-auto flex size-11 items-center justify-center rounded-2xl border border-border/70 bg-background">
              <Orbit className="size-5 text-foreground" />
            </div>
            <p className="text-sm font-medium text-foreground">仿真画布插槽</p>
            <p className="text-xs leading-5 text-muted-foreground">
              后续这里接 MuJoCo 预览、状态流和任务执行回放。
            </p>
          </div>
        </div>
        <div className="grid gap-2">
          <MetricRow icon={Boxes} label="Scene Bridge" value="Waiting" />
          <MetricRow icon={Cpu} label="Physics Runtime" value="Not attached" />
          <MetricRow icon={Sparkles} label="Task Playback" value="Shell only" />
        </div>
      </div>
    </RobotPanelCard>
  )
}

export function ReachyStatusPanel() {
  const { settings } = useSettings()
  const robotSettings = {
    ...getDefaultRobotWorkbenchSettings(),
    ...(settings.robot_settings || {}),
  }
  const { connectionState, snapshot, daemonBaseUrl, error, lastUpdatedAt } = useReachyStatus(robotSettings)
  const headPose = getEulerPose(snapshot?.head_pose)

  const panelStatus =
    connectionState === "disabled"
      ? "Disabled"
      : connectionState === "live"
        ? "Live"
        : connectionState === "connecting"
          ? "Connecting"
          : "Offline"
  const robotLinkValue =
    connectionState === "disabled"
      ? "Disabled"
      : connectionState === "live"
        ? "Connected"
        : connectionState === "connecting"
          ? "Connecting"
          : "Disconnected"
  const streamValue =
    connectionState === "live"
      ? "Streaming"
      : connectionState === "connecting"
        ? "Opening"
        : connectionState === "disabled"
          ? "Off"
          : "Idle"
  const poseSummary = headPose
    ? `Yaw ${formatRadiansAsDegrees(headPose.yaw)} | Pitch ${formatRadiansAsDegrees(headPose.pitch)} | Roll ${formatRadiansAsDegrees(headPose.roll)}`
    : "Waiting for the first Reachy state frame"

  return (
    <RobotPanelCard
      testId="reachy-status-panel"
      eyebrow="Robot"
      title="Reachy Status"
      description="实时订阅 Reachy daemon 状态流，先做只读状态镜像，命令控制仍留在下一轮。"
      status={panelStatus}
      statusTone={connectionState === "live" ? "success" : "neutral"}
    >
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div className="grid gap-2 md:grid-cols-2">
          <MetricRow icon={Bot} label="Robot Link" value={robotLinkValue} />
          <MetricRow icon={RadioTower} label="State Stream" value={streamValue} />
          <MetricRow icon={Cpu} label="Control Mode" value={snapshot?.control_mode ?? "—"} />
          <MetricRow icon={Sparkles} label="Head Yaw" value={formatRadiansAsDegrees(headPose?.yaw)} />
        </div>
        <div className="rounded-2xl border border-border/60 bg-muted/20 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Live Mirror
          </p>
          <div className="mt-3 space-y-2 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Daemon</span>
              <span className="max-w-[220px] truncate text-muted-foreground">{daemonBaseUrl}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Body yaw</span>
              <span className="text-muted-foreground">{formatRadiansAsDegrees(snapshot?.body_yaw)}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Antennas</span>
              <span className="text-muted-foreground">{formatAntennaPair(snapshot?.antennas_position)}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-foreground">Last update</span>
              <span className="text-muted-foreground">{formatTimestamp(lastUpdatedAt)}</span>
            </div>
          </div>
        </div>
        <div className="rounded-2xl border border-border/60 bg-background/70 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Head Pose
          </p>
          <p className="mt-3 text-xs text-foreground">{poseSummary}</p>
          {headPose ? (
            <p className="mt-2 text-[11px] text-muted-foreground">
              X {headPose.x.toFixed(3)} | Y {headPose.y.toFixed(3)} | Z {headPose.z.toFixed(3)}
            </p>
          ) : null}
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
        <div className="rounded-2xl border border-border/60 bg-muted/20 px-3 py-2.5 text-xs text-muted-foreground">
          当前版本只镜像 Reachy 状态，不发送控制命令。
        </div>
      </div>
    </RobotPanelCard>
  )
}

export function RobotSidePanel({ projectName }: { projectName: string }) {
  return (
    <aside
      className="flex w-[360px] min-w-[320px] max-w-[420px] shrink-0 flex-col border-l border-border/70 bg-muted/18"
      data-testid="robot-side-panel"
    >
      <div className="border-b border-border/70 px-4 py-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          Robot Workbench
        </p>
        <div className="mt-1 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">{projectName}</p>
            <p className="text-xs text-muted-foreground">MuJoCo + Reachy 桌面集成壳子</p>
          </div>
          <span className="inline-flex items-center rounded-full border border-border/70 bg-background px-2 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
            Shell
          </span>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-3">
        <MujocoPanel />
        <ReachyStatusPanel />
      </div>
    </aside>
  )
}
