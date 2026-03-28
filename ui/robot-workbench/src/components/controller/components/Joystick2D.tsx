import { memo, useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import { cn } from "@/lib/utils"

interface Joystick2DProps {
  label: string
  valueX: number
  valueY: number
  onChange: (x: number, y: number, continuous: boolean) => void
  onDragEnd?: () => void
  minX?: number
  maxX?: number
  minY?: number
  maxY?: number
  size?: number
  disabled?: boolean
  smoothedValueX?: number
  smoothedValueY?: number
  labelAlign?: "left" | "right"
}

const STICK_RADIUS = 8

function clampToUnitCircle(x: number, y: number) {
  const distance = Math.sqrt(x * x + y * y)
  if (distance <= 1) return { x, y }
  return { x: x / distance, y: y / distance }
}

const Joystick2D = memo(function Joystick2D({
  label,
  valueX,
  valueY,
  onChange,
  onDragEnd,
  minX = -1,
  maxX = 1,
  minY = -1,
  maxY = 1,
  size = 112,
  disabled = false,
  smoothedValueX,
  smoothedValueY,
  labelAlign = "left",
}: Joystick2DProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [localStickX, setLocalStickX] = useState(size / 2)
  const [localStickY, setLocalStickY] = useState(size / 2)
  const centerX = size / 2
  const centerY = size / 2
  const maxRadius = size / 2 - 16
  const lastDragEndTimeRef = useRef(0)

  useEffect(() => {
    const isAtZero = Math.abs(valueX) < 0.0001 && Math.abs(valueY) < 0.0001

    if (isAtZero) {
      setLocalStickX(centerX)
      setLocalStickY(centerY)
      if (isDragging) {
        setIsDragging(false)
        lastDragEndTimeRef.current = Date.now()
      }
      return
    }

    if (!isDragging) {
      const timeSinceDragEnd = Date.now() - lastDragEndTimeRef.current
      if (timeSinceDragEnd >= 500) {
        const normalizedX = ((valueX - minX) / (maxX - minX)) * 2 - 1
        const normalizedY = 1 - ((valueY - minY) / (maxY - minY)) * 2
        const clamped = clampToUnitCircle(normalizedX, normalizedY)
        const nextStickX = centerX + clamped.x * maxRadius
        const nextStickY = centerY - clamped.y * maxRadius
        setLocalStickX(nextStickX)
        setLocalStickY(nextStickY)
      }
    }
  }, [centerX, centerY, isDragging, maxRadius, maxX, maxY, minX, minY, valueX, valueY])

  const getValuesFromPointer = useCallback(
    (clientX: number, clientY: number) => {
      if (!containerRef.current) {
        return { x: valueX, y: valueY }
      }

      const rect = containerRef.current.getBoundingClientRect()
      const pointerX = clientX - rect.left
      const pointerY = clientY - rect.top
      const dx = pointerX - centerX
      const dy = pointerY - centerY
      const distance = Math.sqrt(dx * dx + dy * dy)

      let displayX = pointerX
      let displayY = pointerY

      if (distance > maxRadius) {
        const angle = Math.atan2(dy, dx)
        displayX = centerX + Math.cos(angle) * maxRadius
        displayY = centerY + Math.sin(angle) * maxRadius
      }

      setLocalStickX(displayX)
      setLocalStickY(displayY)

      const clampedDx = displayX - centerX
      const clampedDy = displayY - centerY
      const normalizedX = clampedDx / maxRadius
      const normalizedY = clampedDy / maxRadius

      return {
        x: minX + ((normalizedX + 1) / 2) * (maxX - minX),
        y: minY + ((normalizedY + 1) / 2) * (maxY - minY),
      }
    },
    [centerX, centerY, maxRadius, maxX, maxY, minX, minY, valueX, valueY]
  )

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled) return

    event.preventDefault()
    setIsDragging(true)
    const next = getValuesFromPointer(event.clientX, event.clientY)
    onChange(next.x, next.y, true)
  }

  const handlePointerMove = useCallback(
    (event: PointerEvent) => {
      if (!isDragging) return

      event.preventDefault()
      const next = getValuesFromPointer(event.clientX, event.clientY)
      onChange(next.x, next.y, true)
    },
    [getValuesFromPointer, isDragging, onChange]
  )

  const handlePointerUp = useCallback(() => {
    if (!isDragging) return

    setIsDragging(false)
    lastDragEndTimeRef.current = Date.now()
    onDragEnd?.()
  }, [isDragging, onDragEnd])

  useEffect(() => {
    if (!isDragging) return

    window.addEventListener("pointermove", handlePointerMove, { passive: false })
    window.addEventListener("pointerup", handlePointerUp)

    return () => {
      window.removeEventListener("pointermove", handlePointerMove)
      window.removeEventListener("pointerup", handlePointerUp)
    }
  }, [handlePointerMove, handlePointerUp, isDragging])

  const ghost = (() => {
    if (typeof smoothedValueX !== "number" || typeof smoothedValueY !== "number") return null
    const normalizedX = ((smoothedValueX - minX) / (maxX - minX)) * 2 - 1
    const normalizedY = 1 - ((smoothedValueY - minY) / (maxY - minY)) * 2
    const clamped = clampToUnitCircle(normalizedX, normalizedY)
    return {
      x: centerX + clamped.x * maxRadius,
      y: centerY - clamped.y * maxRadius,
    }
  })()

  return (
    <div className={cn("flex flex-col gap-2", labelAlign === "right" && "items-end")}>
      <div className={cn("space-y-0.5", labelAlign === "right" && "text-right")}>
        <p className="text-[11px] font-semibold text-foreground">{label}</p>
        <p className="font-mono text-[10px] text-muted-foreground">
          X {valueX.toFixed(3)} Y {valueY.toFixed(3)}
        </p>
      </div>
      <div
        ref={containerRef}
        className={cn(
          "relative overflow-hidden rounded-[14px] border border-border/70 bg-[radial-gradient(circle_at_top,_rgba(245,158,11,0.12),_transparent_72%)] select-none",
          disabled ? "cursor-not-allowed opacity-50" : isDragging ? "cursor-grabbing" : "cursor-grab"
        )}
        style={{ width: size, height: size }}
        onPointerDown={handlePointerDown}
      >
        <svg width={size} height={size} className="block">
          <line x1={centerX} y1={0} x2={centerX} y2={size} stroke="rgba(245,158,11,0.28)" strokeWidth={1} />
          <line x1={0} y1={centerY} x2={size} y2={centerY} stroke="rgba(245,158,11,0.28)" strokeWidth={1} />
          <circle
            cx={centerX}
            cy={centerY}
            r={maxRadius}
            fill="none"
            stroke="rgba(245,158,11,0.35)"
            strokeWidth={1.5}
            strokeDasharray="3 4"
          />
          {ghost ? (
            <>
              <line
                x1={centerX}
                y1={centerY}
                x2={ghost.x}
                y2={ghost.y}
                stroke="rgba(245,158,11,0.35)"
                strokeWidth={1.5}
                strokeDasharray="3 3"
              />
              <circle
                cx={ghost.x}
                cy={ghost.y}
                r={STICK_RADIUS * 0.82}
                fill="rgba(245,158,11,0.18)"
                stroke="rgba(245,158,11,0.55)"
                strokeWidth={1.5}
              />
            </>
          ) : null}
          <line
            x1={centerX}
            y1={centerY}
            x2={localStickX}
            y2={localStickY}
            stroke="#f59e0b"
            strokeWidth={2}
            strokeLinecap="round"
            opacity={0.55}
          />
          <circle
            cx={localStickX}
            cy={localStickY}
            r={STICK_RADIUS}
            fill="#f59e0b"
            stroke="rgba(255,255,255,0.9)"
            strokeWidth={1.5}
          />
        </svg>
      </div>
    </div>
  )
})

export default Joystick2D
