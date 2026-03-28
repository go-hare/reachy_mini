import { type ReactNode } from "react"
import { Activity, RotateCcw, Waypoints } from "lucide-react"
import { Button } from "@/components/ui/button"
import { ControllerProvider } from "@/components/controller/context/ControllerContext"
import CircularSlider from "@/components/controller/components/CircularSlider"
import Joystick2D from "@/components/controller/components/Joystick2D"
import SimpleSlider from "@/components/controller/components/SimpleSlider"
import VerticalSlider from "@/components/controller/components/VerticalSlider"
import { useControllerAPI, type ControllerTransportState } from "@/components/controller/hooks/useControllerAPI"
import { useControllerHandlers } from "@/components/controller/hooks/useControllerHandlers"
import { useControllerSmoothing } from "@/components/controller/hooks/useControllerSmoothing"
import { useControllerSync } from "@/components/controller/hooks/useControllerSync"
import { EXTENDED_ROBOT_RANGES } from "@/components/controller/utils/inputConstants"
import { mapDisplayToRobot, mapRobotToDisplay } from "@/components/controller/utils/inputMappings"
import type { ReachyConnectionState, ReachyFullState } from "@/lib/reachy-daemon"

interface ReachyControllerProps {
  daemonBaseUrl: string
  snapshot: ReachyFullState | null
  syncState: ReachyConnectionState
  isActive?: boolean
}

function formatTransportLabel(state: ControllerTransportState) {
  switch (state) {
    case "websocket":
      return "WebSocket"
    case "http":
      return "HTTP Fallback"
    case "connecting":
      return "Connecting"
    case "offline":
      return "Offline"
    default:
      return "Disabled"
  }
}

function formatSyncLabel(state: ReachyConnectionState) {
  switch (state) {
    case "live":
      return "Streaming"
    case "connecting":
      return "Opening"
    case "offline":
      return "Lost"
    default:
      return "Blind"
  }
}

function ControllerInner({
  daemonBaseUrl,
  snapshot,
  syncState,
  isActive = true,
}: ReachyControllerProps) {
  const { transportState, error, sendCommand, forceSendCommand } = useControllerAPI({
    daemonBaseUrl,
    enabled: isActive,
  })
  const {
    localValues,
    handleChange,
    handleBodyYawChange,
    handleAntennasChange,
    handleDragEnd,
    resetAllValues,
  } = useControllerHandlers({ sendCommand: forceSendCommand })
  const { smoothedValues } = useControllerSmoothing({ sendCommand })

  useControllerSync({
    snapshot,
    enabled: syncState === "live",
  })

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-2 md:grid-cols-2">
        <MetricTile label="Command" value={formatTransportLabel(transportState)} />
        <MetricTile label="Sync" value={formatSyncLabel(syncState)} />
        <MetricTile label="Daemon" value={daemonBaseUrl.replace(/^https?:\/\//, "")} />
        <MetricTile label="Control Mode" value={snapshot?.control_mode ?? "—"} />
      </div>

      <section className="space-y-2.5">
        <div className="flex items-center justify-between gap-3">
          <SectionHeading icon={Activity} label="Antennas" />
          <Button size="sm" variant="outline" onClick={() => void resetAllValues()}>
            <RotateCcw className="size-4" />
            回正
          </Button>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <ControlCard>
            <CircularSlider
              label="Left"
              value={localValues.antennas[0] ?? 0}
              smoothedValue={smoothedValues.antennas[0]}
              onChange={(nextValue, continuous) => handleAntennasChange("left", nextValue, continuous)}
              min={-Math.PI}
              max={Math.PI}
              unit="rad"
              step={0.01}
            />
          </ControlCard>
          <ControlCard>
            <CircularSlider
              label="Right"
              value={localValues.antennas[1] ?? 0}
              smoothedValue={smoothedValues.antennas[1]}
              onChange={(nextValue, continuous) => handleAntennasChange("right", nextValue, continuous)}
              min={-Math.PI}
              max={Math.PI}
              unit="rad"
              alignRight
              step={0.01}
            />
          </ControlCard>
        </div>
      </section>

      <section className="space-y-2.5">
        <SectionHeading icon={Waypoints} label="Head" />
        <div className="grid gap-3 md:grid-cols-2">
          <ControlCard>
            <div className="flex items-center gap-4">
              <Joystick2D
                label="Position X/Y"
                valueX={mapRobotToDisplay(localValues.headPose.y, "positionY")}
                valueY={mapRobotToDisplay(localValues.headPose.x, "positionX")}
                smoothedValueX={mapRobotToDisplay(smoothedValues.headPose.y, "positionY")}
                smoothedValueY={mapRobotToDisplay(smoothedValues.headPose.x, "positionX")}
                onChange={(nextX, nextY, continuous) => {
                  const robotY = mapDisplayToRobot(nextX, "positionY")
                  const robotX = mapDisplayToRobot(nextY, "positionX")
                  handleChange({ x: robotX, y: robotY }, continuous)
                }}
                onDragEnd={handleDragEnd}
                minX={EXTENDED_ROBOT_RANGES.POSITION.min}
                maxX={EXTENDED_ROBOT_RANGES.POSITION.max}
                minY={EXTENDED_ROBOT_RANGES.POSITION.min}
                maxY={EXTENDED_ROBOT_RANGES.POSITION.max}
              />
              <VerticalSlider
                label="Position Z"
                value={localValues.headPose.z}
                smoothedValue={smoothedValues.headPose.z}
                onChange={(nextValue, continuous) => handleChange({ z: nextValue }, continuous)}
                min={-0.05}
                max={0.05}
                unit="m"
                centered
              />
            </div>
          </ControlCard>

          <ControlCard>
            <Joystick2D
              label="Pitch / Yaw"
              valueX={mapRobotToDisplay(localValues.headPose.yaw, "yaw")}
              valueY={mapRobotToDisplay(localValues.headPose.pitch, "pitch")}
              smoothedValueX={mapRobotToDisplay(smoothedValues.headPose.yaw, "yaw")}
              smoothedValueY={mapRobotToDisplay(smoothedValues.headPose.pitch, "pitch")}
              onChange={(nextYaw, nextPitch, continuous) => {
                const robotYaw = mapDisplayToRobot(nextYaw, "yaw")
                const robotPitch = mapDisplayToRobot(nextPitch, "pitch")
                handleChange({ yaw: robotYaw, pitch: robotPitch }, continuous)
              }}
              onDragEnd={handleDragEnd}
              minX={EXTENDED_ROBOT_RANGES.YAW.min}
              maxX={EXTENDED_ROBOT_RANGES.YAW.max}
              minY={EXTENDED_ROBOT_RANGES.PITCH.min}
              maxY={EXTENDED_ROBOT_RANGES.PITCH.max}
              labelAlign="right"
            />
          </ControlCard>
        </div>
        <ControlCard>
          <SimpleSlider
            label="Roll"
            value={localValues.headPose.roll}
            smoothedValue={smoothedValues.headPose.roll}
            onChange={(nextValue, continuous) => handleChange({ roll: nextValue }, continuous)}
            min={-0.5}
            max={0.5}
            showRollVisualization
          />
        </ControlCard>
      </section>

      <section className="space-y-2.5">
        <SectionHeading icon={Waypoints} label="Body" />
        <ControlCard>
          <CircularSlider
            label="Yaw"
            value={localValues.bodyYaw}
            smoothedValue={smoothedValues.bodyYaw}
            onChange={(nextValue, continuous) => handleBodyYawChange(nextValue, continuous)}
            min={(-160 * Math.PI) / 180}
            max={(160 * Math.PI) / 180}
            unit="rad"
            inverted
            reverse
            step={0.01}
          />
        </ControlCard>
      </section>

      {syncState === "disabled" ? (
        <div className="rounded-2xl border border-border/70 bg-muted/20 px-3 py-2.5 text-xs text-muted-foreground">
          状态流已关闭，当前只发命令不回读。
        </div>
      ) : null}

      {error ? (
        <div className="rounded-2xl border border-[hsl(var(--warning))]/30 bg-[hsl(var(--warning))]/10 px-3 py-2.5 text-xs text-[hsl(var(--warning))]">
          {error}
        </div>
      ) : null}
    </div>
  )
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-muted/25 px-3 py-2.5">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 truncate text-xs font-medium text-foreground">{value}</p>
    </div>
  )
}

function SectionHeading({
  icon: Icon,
  label,
}: {
  icon: typeof Activity
  label: string
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex size-6 items-center justify-center rounded-lg border border-border/70 bg-background/90">
        <Icon className="size-3.5 text-muted-foreground" />
      </div>
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
    </div>
  )
}

function ControlCard({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-border/60 bg-background/80 p-2.5">
      {children}
    </div>
  )
}

export default function ReachyController(props: ReachyControllerProps) {
  if (!props.isActive && props.isActive !== undefined) {
    return null
  }

  return (
    <ControllerProvider isActive={props.isActive ?? true}>
      <ControllerInner {...props} />
    </ControllerProvider>
  )
}
