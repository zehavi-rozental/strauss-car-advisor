// Geometry of the opening artwork: an imaginary fastback, never a real model, seen from the side and shown
// only through the air that flows over it (wind-tunnel smoke lines). Units are car lengths: u = 0 at the
// nose (screen left, the direction a Hebrew reader moves in), u = 1 at the tail; heights above the ground.

type Pt = [number, number]

/** Top contour, nose to tail. */
const TOP: Pt[] = [
  [0.0, 0.09], [0.004, 0.138], [0.02, 0.172], [0.065, 0.192], [0.18, 0.21], [0.3, 0.228], [0.4, 0.284],
  [0.48, 0.318], [0.58, 0.331], [0.69, 0.32], [0.8, 0.283], [0.9, 0.249], [0.956, 0.241], [0.986, 0.228],
  [0.999, 0.196], [1.0, 0.12],
]

export const WHEELS = [
  { u: 0.168, r: 0.072 },
  { u: 0.812, r: 0.072 },
]
const ARCH = 0.088
const SILL = 0.07

/** Catmull-Rom through the points, sampled densely. */
function spline(points: Pt[], perSegment = 14): Pt[] {
  const out: Pt[] = []
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)], p1 = points[i], p2 = points[i + 1], p3 = points[Math.min(points.length - 1, i + 2)]
    for (let s = 0; s < perSegment; s++) {
      const t = s / perSegment, t2 = t * t, t3 = t2 * t
      const f = (a: number, b: number, c: number, d: number) =>
        0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3)
      out.push([f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1])])
    }
  }
  out.push(points[points.length - 1])
  return out
}

export const TOP_CURVE: Pt[] = spline(TOP)

/** Height of the body's top at u (0 outside the car). */
export function topAt(u: number): number {
  if (u < 0 || u > 1) return 0
  const c = TOP_CURVE
  let lo = 0, hi = c.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (c[mid][0] <= u) lo = mid
    else hi = mid
  }
  const [x0, y0] = c[lo], [x1, y1] = c[hi]
  return x1 === x0 ? Math.max(y0, y1) : y0 + ((u - x0) / (x1 - x0)) * (y1 - y0)
}

/** The closed body outline: top contour, the tail, the rear arch, the sill, the front arch, the nose. */
export function bodyOutline(): Pt[] {
  const pts: Pt[] = [...TOP_CURVE]
  pts.push([0.994, 0.092], [0.94, 0.076])
  const arch = (cu: number, from: number, to: number) => {
    for (let k = 0; k <= 18; k++) {
      const a = from + ((to - from) * k) / 18
      pts.push([cu + Math.cos(a) * ARCH, WHEELS[0].r + Math.sin(a) * ARCH])
    }
  }
  // arcs run from the rear side of each arch over the top to its front side (angles from +x, counter-clockwise)
  arch(WHEELS[1].u, -0.12, Math.PI + 0.12)
  pts.push([WHEELS[1].u - ARCH - 0.01, SILL], [WHEELS[0].u + ARCH + 0.01, SILL])
  arch(WHEELS[0].u, -0.12, Math.PI + 0.12)
  pts.push([0.035, 0.08], [0.0, 0.09])
  return pts
}

/** Side glass: between the belt line and the roof, set in from the body edge. */
export function glassOutline(): Pt[] {
  const belt = (u: number) => 0.226 + (u - 0.33) * 0.05
  const top: Pt[] = []
  for (let u = 0.33; u <= 0.835; u += 0.005) {
    const y = topAt(u) - 0.024
    if (y > belt(u) + 0.004) top.push([u, y])
  }
  if (top.length < 2) return []
  const bottom: Pt[] = [[top[top.length - 1][0], belt(top[top.length - 1][0])], [top[0][0], belt(top[0][0])]]
  return [...top, ...bottom]
}

/** Smoothed displacement the body imposes on the air above it: never below the body (plus a gap). */
export function displacement(grid: { from: number; to: number; step: number }, gap = 0.012, sigma = 0.075): {
  at: (u: number) => number
} {
  const n = Math.ceil((grid.to - grid.from) / grid.step) + 1
  const raw = new Float32Array(n)
  for (let i = 0; i < n; i++) raw[i] = topAt(grid.from + i * grid.step)
  const radius = Math.ceil((sigma * 3) / grid.step)
  const kernel: number[] = []
  let sum = 0
  for (let k = -radius; k <= radius; k++) {
    const w = Math.exp(-((k * grid.step) ** 2) / (2 * sigma * sigma))
    kernel.push(w)
    sum += w
  }
  const out = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    let acc = 0
    for (let k = -radius; k <= radius; k++) {
      const j = i + k
      if (j >= 0 && j < n) acc += raw[j] * kernel[k + radius]
    }
    const blurred = (acc / sum) * 1.12
    out[i] = Math.max(raw[i] > 0 ? raw[i] + gap : 0, blurred)
    // the wake: behind the tail the air comes down slowly instead of falling off the rear face
    const u = grid.from + i * grid.step
    if (u > 0.95) out[i] = Math.max(out[i], (topAt(0.95) + gap) * Math.exp(-(((u - 0.95) / 0.5) ** 1.4)))
  }
  return {
    at(u: number) {
      const f = (u - grid.from) / grid.step
      if (f <= 0) return out[0]
      if (f >= n - 1) return out[n - 1]
      const i = Math.floor(f)
      return out[i] + (out[i + 1] - out[i]) * (f - i)
    },
  }
}

/** Base heights of the streamlines: dense near the body, opening up with height. */
export function lineHeights(count: number, first = 0.017): number[] {
  const hs: number[] = []
  let h = 0
  for (let i = 0; i < count; i++) {
    hs.push(h)
    h += first * (1 + i * 0.085)
  }
  return hs
}

/** Height of streamline h above the ground at u: lifted by the body, the lift fading with height. */
export function streamY(h: number, lift: number, falloff = 0.55): number {
  return h + lift * Math.exp(-h / falloff)
}
