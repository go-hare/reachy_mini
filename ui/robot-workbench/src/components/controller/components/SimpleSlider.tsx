import { memo, useEffect, useMemo, useState } from "react"

interface SimpleSliderProps {
  label: string
  value: number
  onChange: (value: number, continuous: boolean) => void
  min?: number
  max?: number
  unit?: string
  disabled?: boolean
  centered?: boolean
  showRollVisualization?: boolean
  smoothedValue?: number
  step?: number
  compact?: boolean
}

const SimpleSlider = memo(function SimpleSlider({
  label,
  value,
  onChange,
  min = -1,
  max = 1,
  unit = "rad",
  disabled = false,
  centered = false,
  showRollVisualization = false,
  smoothedValue,
  step = 0.01,
  compact = false,
}: SimpleSliderProps) {
  const [draftValue, setDraftValue] = useState(value)

  useEffect(() => {
    setDraftValue(value)
  }, [value])

  const displayValue = typeof value === "number" ? value.toFixed(unit === "deg" ? 1 : 3) : "0.000"
  const ghostLeft =
    typeof smoothedValue === "number" ? `${((smoothedValue - min) / (max - min)) * 100}%` : null

  const rollVisualization = useMemo(() => {
    if (!showRollVisualization) return null

    const width = compact ? 32 : 38
    const height = compact ? 18 : 20
    const padding = 4
    const normalized = (value - min) / (max - min)
    const startX = width - padding
    const startY = height - padding
    const endX = padding
    const endY = height - padding
    const controlX = width / 2
    const controlY = padding + (height - padding * 2) * 0.2
    const approximateLength = 44

    return {
      width,
      height,
      padding,
      startX,
      startY,
      endX,
      endY,
      controlX,
      controlY,
      strokeDashoffset: approximateLength * normalized,
      approximateLength,
    }
  }, [max, min, showRollVisualization, value])

  return (
    <div className={`flex flex-col ${compact ? "gap-1.5" : "gap-2"}`}>
      <div className={centered ? "space-y-0.5 text-center" : `flex items-center ${compact ? "gap-2" : "gap-2.5"}`}>
        <p className={compact ? "text-[10px] font-semibold text-foreground" : "text-[11px] font-semibold text-foreground"}>{label}</p>
        <p className={compact ? "font-mono text-[9px] text-muted-foreground" : "font-mono text-[10px] text-muted-foreground"}>
          {displayValue}
          {unit === "deg" ? "deg" : ` ${unit}`}
        </p>
      </div>
      <div className={`flex items-center ${compact ? "gap-2" : "gap-3"}`}>
        {rollVisualization ? (
          <svg
            viewBox={`-${rollVisualization.padding} -${rollVisualization.padding} ${rollVisualization.width + rollVisualization.padding * 2} ${rollVisualization.height + rollVisualization.padding * 2}`}
            style={{ width: rollVisualization.width, height: rollVisualization.height }}
            className="shrink-0"
          >
            <path
              d={`M ${rollVisualization.startX} ${rollVisualization.startY} Q ${rollVisualization.controlX} ${rollVisualization.controlY} ${rollVisualization.endX} ${rollVisualization.endY}`}
              fill="none"
              stroke="rgba(148,163,184,0.26)"
              strokeWidth={4}
              strokeLinecap="round"
            />
            <path
              d={`M ${rollVisualization.startX} ${rollVisualization.startY} Q ${rollVisualization.controlX} ${rollVisualization.controlY} ${rollVisualization.endX} ${rollVisualization.endY}`}
              fill="none"
              stroke="rgba(15,23,42,0.52)"
              strokeWidth={3}
              strokeLinecap="round"
              strokeDasharray={rollVisualization.approximateLength}
              strokeDashoffset={rollVisualization.strokeDashoffset}
            />
          </svg>
        ) : null}
        <div className="relative flex-1">
          {ghostLeft ? (
            <span
              className="pointer-events-none absolute top-1/2 z-0 h-3 w-3 rounded-full border border-amber-400/60 bg-amber-400/20"
              style={{ left: ghostLeft, transform: "translate(-50%, -50%)" }}
            />
          ) : null}
          <input
            type="range"
            min={min}
            max={max}
            step={step}
            value={draftValue}
            disabled={disabled}
            className="relative z-10 w-full accent-amber-500"
            onChange={(event) => {
              const nextValue = Number(event.currentTarget.value)
              setDraftValue(nextValue)
              onChange(nextValue, true)
            }}
            onPointerUp={() => onChange(draftValue, false)}
            onKeyUp={() => onChange(draftValue, false)}
          />
        </div>
      </div>
    </div>
  )
})

export default SimpleSlider
