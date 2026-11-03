/**
 * Chart theme shared by every Recharts chart.
 *
 * Adapted from OpenIntelligentUI @ 8391a94c (MIT, Copyright (c) Atai Barkai):
 * apps/app/src/components/generative-ui/charts/config.ts (CHART_CONFIG.tooltipStyle reading the
 * --chart-tooltip-* CSS variables) and bar-chart.tsx (axis stroke from --chart-axis, 12px ticks).
 * Aegis changes: the seven-colour categorical palette is replaced by restrained mint / lilac /
 * teal series tokens; animations are disabled (live data must not ease between values) and
 * missing samples are never connected.
 */
export const CHART_SERIES = ['var(--chart-series-1)', 'var(--chart-series-2)', 'var(--chart-series-3)'] as const;

export const CHART_CONFIG = {
  tooltipStyle: {
    backgroundColor: 'var(--chart-tooltip-bg)',
    border: '1px solid var(--chart-tooltip-border)',
    borderRadius: '8px',
    padding: '8px 12px',
    color: 'var(--text-primary)',
  },
  axis: { stroke: 'var(--chart-axis)', tick: { fill: 'var(--chart-axis)', fontSize: 11 }, tickLine: false, axisLine: false },
  grid: { stroke: 'var(--chart-grid)', strokeDasharray: '3 5', vertical: false },
  line: { isAnimationActive: false, connectNulls: false, dot: false },
} as const;
