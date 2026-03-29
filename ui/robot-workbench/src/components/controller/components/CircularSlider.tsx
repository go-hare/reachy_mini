import { memo, useEffect, useMemo, useState } from "react"

interface CircularSliderProps {
  label: string
  value: number
  onChange: (value: number, continuous: boolean) => void
  min?: number
  max?: number
  unit?: string
  disabled?: boolean
  inverted?: boolean
  reverse?: boolean
  alignRight?: boolean
  smoothedValue?: number
  step?: number
  compact?: boolean
}

const CircularSlider = memo(function CircularSlider({
  label,
  value,
  onChange,
  min = -Math.PI,
  max = Math.PI,
  unit = "rad",
  disabled = false,
  inverted = false,
  reverse = false,
  alignRight = false,
  smoothedValue,
  step = 0.1,
  compact = false,
}: CircularSliderProps) {
  const [draftValue, setDraftValue] = useState(value)

  useEffect(() => {
    setDraftValue(value)
  }, [value])

  const displayValue = typeof value === "number" ? value.toFixed(unit === "deg" ? 1 : 3) : "0.000"
  const ghostLeft =
    typeof smoothedValue === "number" ? `${((smoothedValue - min) / (max - min)) * 100}%` : null

  const svgCalculations = useMemo(() => {
    const arcStart = 0.01
    const arcEnd = 0.74
    const arcSpan = arcEnd - arcStart
    const arcDegrees = 270
    const radius = compact ? 16 : 18
    const border = compact ? 4 : 5
    const circleRadius = radius - border / 2
    const circumference = 2 * Math.PI * circleRadius
    const internalValue = arcStart + ((value - min) / (max - min)) * arcSpan
    const effectiveInternalValue = reverse ? arcEnd - (internalValue - arcStart) : internalValue

    return {
      radius,
      border,
      circleRadius,
      circumference,
      svgRotation: inverted ? -45 : 135,
      strokeDashoffset: circumference * (1 - effectiveInternalValue),
      totalStrokeDashoffset: circumference * (1 - arcEnd),
      innerStrokeWidth: border / 1.1,
      strokeWidth: border,
      arcDegrees,
    }
  }, [inverted, max, min, reverse, value])

  return (
    <div className={`flex flex-col ${compact ? "gap-1.5" : "gap-2"}`}>
      <div
        className={`flex items-center ${compact ? "gap-2" : "gap-2.5"} ${alignRight ? "justify-end flex-row-reverse" : ""}`}
      >
        <p className={compact ? "text-[10px] font-semibold text-foreground" : "text-[11px] font-semibold text-foreground"}>{label}</p>
        <p className={compact ? "font-mono text-[9px] text-muted-foreground" : "font-mono text-[10px] text-muted-foreground"}>
          {displayValue}
          {unit === "deg" ? "deg" : ` ${unit}`}
        </p>
      </div>
      <div className={`flex items-center ${compact ? "gap-2" : "gap-3"} ${alignRight ? "flex-row-reverse" : ""}`}>
        <svg
          viewBox={`0 0 ${svgCalculations.radius * 2} ${svgCalculations.radius * 2}`}
          style={{
            width: svgCalculations.radius * 2,
            height: svgCalculations.radius * 2,
            transform: `rotate(${svgCalculations.svgRotation}deg)`,
          }}
          className="shrink-0"
        >
          <circle
            stroke="rgba(148,163,184,0.22)"
            strokeLinecap="round"
            fill="none"
            strokeWidth={svgCalculations.strokeWidth}
            strokeDashoffset={svgCalculations.totalStrokeDashoffset}
            strokeDasharray={svgCalculations.circumference}
            r={svgCalculations.circleRadius}
            cx={svgCalculations.radius}
            cy={svgCalculations.radius}
          />
          <circle
            stroke="rgba(15,23,42,0.52)"
            strokeLinecap="round"
            fill="none"
            strokeWidth={svgCalculations.innerStrokeWidth}
            strokeDashoffset={svgCalculations.strokeDashoffset}
            strokeDasharray={svgCalculations.circumference}
            r={svgCalculations.circleRadius}
            cx={svgCalculations.radius}
            cy={svgCalculations.radius}
          />
        </svg>
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

export default CircularSlider
