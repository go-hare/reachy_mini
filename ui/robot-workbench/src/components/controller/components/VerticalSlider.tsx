import { memo, useEffect, useState } from "react"

interface VerticalSliderProps {
  label: string
  value: number
  onChange: (value: number, continuous: boolean) => void
  min?: number
  max?: number
  unit?: string
  disabled?: boolean
  centered?: boolean
  smoothedValue?: number
  height?: number
  step?: number
}

const VerticalSlider = memo(function VerticalSlider({
  label,
  value,
  onChange,
  min = -1,
  max = 1,
  unit = "m",
  disabled = false,
  centered = false,
  smoothedValue,
  height = 120,
  step = 0.001,
}: VerticalSliderProps) {
  const [draftValue, setDraftValue] = useState(value)

  useEffect(() => {
    setDraftValue(value)
  }, [value])

  const displayValue = typeof value === "number" ? value.toFixed(unit === "deg" ? 1 : 3) : "0.000"
  const ghostTop =
    typeof smoothedValue === "number" ? `${100 - ((smoothedValue - min) / (max - min)) * 100}%` : null

  return (
    <div className="flex flex-col items-center gap-2">
      <div className={centered ? "space-y-0.5 text-center" : "w-full space-y-0.5"}>
        <p className="text-[11px] font-semibold text-foreground">{label}</p>
        <p className="font-mono text-[10px] text-muted-foreground">
          {displayValue}
          {unit === "deg" ? "deg" : ` ${unit}`}
        </p>
      </div>
      <div className="relative flex items-center justify-center" style={{ height }}>
        {ghostTop ? (
          <span
            className="pointer-events-none absolute left-1/2 z-0 h-3 w-3 -translate-x-1/2 rounded-full border border-amber-400/60 bg-amber-400/20"
            style={{ top: ghostTop, transform: "translate(-50%, -50%)" }}
          />
        ) : null}
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={draftValue}
          disabled={disabled}
          className="h-2 accent-amber-500"
          style={{ width: height, transform: "rotate(-90deg)" }}
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
  )
})

export default VerticalSlider
