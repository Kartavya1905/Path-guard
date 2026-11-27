// Exact percent <-> fraction conversion on decimal strings (no floating point), plus
// display helpers. The API stores probabilities as fractions in [0, 1].

import type { Estimate } from './api'

const NUM = /^\d+(\.\d+)?$/

/** Shift a non-negative decimal string's point by `places` (+ right, - left), exactly. */
function shift(num: string, places: number): string {
  const [int = '0', dec = ''] = num.split('.')
  let digits = int + dec
  let scale = dec.length - places  // digits after the point in the result
  if (scale < 0) { digits += '0'.repeat(-scale); scale = 0 }
  digits = digits.padStart(scale + 1, '0')
  const whole = digits.slice(0, digits.length - scale).replace(/^0+(?=\d)/, '')
  const frac = digits.slice(digits.length - scale).replace(/0+$/, '')
  return frac ? `${whole}.${frac}` : whole
}

/** "0.72675" -> "72.675" (exact). */
export function toPercent(fraction: string): string {
  return NUM.test(fraction) ? shift(fraction, 2) : fraction
}

/** "72.675" -> "0.72675" (exact), or null unless a percent from 0 to 100 (8 decimals max). */
export function toFraction(percent: string): string | null {
  const p = percent.trim()
  if (!NUM.test(p) || (p.split('.')[1] ?? '').length > 8) return null
  const f = shift(p, -2)
  return f === '1' || f.startsWith('0') ? f : null
}

/** One-decimal display: "0.72675" -> "72.7%". */
export function pct1(fraction: string | null | undefined): string {
  if (fraction == null) return '?'
  return `${(Math.round(Number(fraction) * 1000) / 10).toFixed(1)}%`
}

/** Exact display: "0.72675" -> "72.675%". */
export function pctExact(fraction: string | null | undefined): string {
  return fraction == null ? '?' : `${toPercent(fraction)}%`
}

/** Short estimate label for map edges: "85%", "80–90%" or "?". */
export function estimateLabel(e: Estimate | undefined): string {
  if (!e || e.kind === 'unknown') return '?'
  const hyp = (e.source as { type?: string } | undefined)?.type === 'hypothetical' ? ' hyp.' : ''
  if (e.kind === 'point') return `${toPercent(String(e.value))}%${hyp}`
  return `${toPercent(String(e.low))}–${toPercent(String(e.high))}%${hyp}`
}

/** Exact sum of fractions with up to 10 decimal places, as a fraction string. */
export function sumFractions(values: string[]): string {
  const total = values.reduce((acc, v) => acc + BigInt(shift(v, 10)), 0n)
  return shift(total.toString(), -10)
}
