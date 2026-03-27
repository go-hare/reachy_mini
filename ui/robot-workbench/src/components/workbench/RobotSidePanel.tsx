import { Bot, Boxes, Cpu, Orbit, RadioTower, Sparkles } from "lucide-react"
import type { ComponentType, ReactNode } from "react"

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
  return (
    <RobotPanelCard
      testId="reachy-status-panel"
      eyebrow="Robot"
      title="Reachy Status"
      description="预留真实机器人连接、状态概览和动作控制区，下一轮再接 websocket 与命令通道。"
      status="Offline"
    >
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div className="grid gap-2 md:grid-cols-2">
          <MetricRow icon={Bot} label="Robot Link" value="Disconnected" />
          <MetricRow icon={RadioTower} label="Command Bus" value="Idle" />
        </div>
        <div className="rounded-2xl border border-border/60 bg-muted/20 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Next Slots
          </p>
          <div className="mt-3 space-y-2">
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="text-foreground">Head pose mirror</span>
              <span className="text-muted-foreground">Not wired</span>
            </div>
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="text-foreground">Motor health</span>
              <span className="text-muted-foreground">Not wired</span>
            </div>
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="text-foreground">Behavior controls</span>
              <span className="text-muted-foreground">Not wired</span>
            </div>
          </div>
        </div>
        <div className="rounded-2xl border border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 px-3 py-2.5 text-xs text-[hsl(var(--warning))]">
          当前阶段只固定桌面结构，不启动 Reachy 后端连接。
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
