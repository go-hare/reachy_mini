import Yoga, * as YogaLayout from 'yoga-layout'

export function getYogaCounters(): {
  visited: number
  measured: number
  cacheHits: number
  live: number
} {
  return {
    visited: 0,
    measured: 0,
    cacheHits: 0,
    live: 0,
  }
}

export default Yoga
export * from 'yoga-layout'
export const {
  Align,
  BoxSizing,
  Dimension,
  Direction,
  Display,
  Edge,
  Errata,
  ExperimentalFeature,
  FlexDirection,
  Gutter,
  Justify,
  MeasureMode,
  Overflow,
  PositionType,
  Unit,
  Wrap,
} = YogaLayout
