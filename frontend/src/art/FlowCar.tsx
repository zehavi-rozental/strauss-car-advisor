// The opening artwork: "רכב מאוויר". An imaginary car is visible only through the air flowing over it, coloured
// like a wind-tunnel pressure map in the Strauss petal colours (fast air turquoise and blue, the warm wake
// orange and red). Decorative only: aria-hidden, no text, never a real or catalog model.
// The car faces the text (screen right): the air comes from the side of the words and its warm wake leaves
// through the empty side, so nothing busy ever runs behind the headline.
// One canvas, a static layer drawn once per size and a light animated layer; it pauses when hidden, off
// screen or when motion is reduced (then a single still frame).
import { useEffect, useRef } from 'react'
import { TOP_CURVE, WHEELS, bodyOutline, displacement, glassOutline, lineHeights, streamY } from './aero'
import { useReducedMotion } from './motion'

export type FlowMode = 'intro' | 'band' | 'login'

interface Layout {
  x0: number; L: number; gy: number; lines: number; alpha: number; particles: number
  /** false: the air only (a narrow band has no room for the car) */
  car?: boolean
  /** fade the air on the text side: full until `from`, down to `min` at `to` (screen x) */
  fade?: { from: number; to: number; min: number }
}

function layoutFor(mode: FlowMode, w: number, h: number): Layout {
  if (mode === 'band') {
    if (w < 720) return { x0: -w, L: w * 1.2, gy: h * 0.96, lines: 22, alpha: 0.55, particles: 1, car: false }
    const L = Math.max(180, Math.min(h * 2.5, w * 0.34, 420))
    return { x0: Math.max(12, w * 0.035), L, gy: h * 0.9, lines: 26, alpha: 0.75, particles: 1 }
  }
  if (mode === 'login') {
    const L = Math.min(Math.max(w * 0.5, 360), 820)
    return { x0: w / 2 - L / 2, L, gy: h * 0.86, lines: 38, alpha: 0.7, particles: 2 }
  }
  if (w >= 900) {
    const L = Math.min(Math.max(w * 0.42, 420), 800)
    const x0 = w * 0.3 - L / 2
    return { x0, L, gy: h * 0.7, lines: 42, alpha: 1, particles: 3,
             fade: { from: x0 + L * 1.06, to: x0 + L * 1.6, min: 0.12 } }
  }
  const L = Math.min(w * 0.9, 560)
  return { x0: (w - L) / 2, L, gy: Math.min(h * 0.4, L * 0.62 + 24), lines: 34, alpha: 1, particles: 2 }
}

const TURQ = [0, 186, 226], BLUE = [3, 144, 203], ORANGE = [244, 131, 38], RED = [237, 48, 36], LEAF = [199, 222, 141]
const rgba = (c: number[], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`

/** Colour of the air at screen x: a pressure map that runs turquoise → blue over the roof → orange → red in the wake. */
/** Car units at screen x (u = 0 at the nose, on the right) and back. */
const uAt = (x: number, lay: Layout) => 1 - (x - lay.x0) / lay.L
const fadeAt = (x: number, lay: Layout) => {
  const f = lay.fade
  if (!f || x <= f.from) return 1
  if (x >= f.to) return f.min
  return 1 - ((x - f.from) / (f.to - f.from)) * (1 - f.min)
}

function airColor(x: number, lay: Layout): number[] {
  const u = uAt(x, lay)
  const mix = (a: number[], b: number[], t: number) => a.map((v, i) => Math.round(v + (b[i] - v) * Math.min(1, Math.max(0, t))))
  if (u < 0.15) return mix(TURQ, TURQ, 0)
  if (u < 0.65) return mix(TURQ, BLUE, (u - 0.15) / 0.5)
  if (u < 1.05) return mix(BLUE, ORANGE, (u - 0.65) / 0.4)
  if (u < 1.45) return mix(ORANGE, RED, (u - 1.05) / 0.4)
  return mix(RED, BLUE, (u - 1.45) / 0.9)
}

export function FlowCar({ mode }: { mode: FlowMode }) {
  const holder = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const reduce = useReducedMotion()

  useEffect(() => {
    const box = holder.current, cv = canvas.current
    if (!box || !cv) return
    const ctx = cv.getContext('2d')
    if (!ctx) return
    let w = 0, h = 0, dpr = 1, lay: Layout = layoutFor(mode, 1, 1)
    let still: HTMLCanvasElement | null = null
    let paths: { y: Float32Array; a: number; width: number; speed: number; h: number }[] = []
    const STEP = 3
    let raf = 0, visible = true, running = false, start = performance.now()
    const parts: { line: number; p: number; len: number }[] = []

    const toY = (yCar: number) => lay.gy - yCar * lay.L
    const toX = (u: number) => lay.x0 + (1 - u) * lay.L

    const build = () => {
      const r = box.getBoundingClientRect()
      w = Math.max(1, Math.round(r.width))
      h = Math.max(1, Math.round(r.height))
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      cv.width = Math.round(w * dpr)
      cv.height = Math.round(h * dpr)
      cv.style.width = `${w}px`
      cv.style.height = `${h}px`
      lay = layoutFor(mode, w, h)

      const lift = lay.car === false ? { at: () => 0 } : displacement({ from: -3, to: 4, step: 0.004 }, 0.012, 0.12)
      const hs = lineHeights(lay.lines)
      paths = hs.map((hh, i) => {
        const y = new Float32Array(Math.ceil(w / STEP) + 1)
        for (let k = 0; k < y.length; k++) {
          y[k] = toY(streamY(hh, lift.at(uAt(k * STEP, lay)), 0.42))
        }
        return { y, h: hh, a: (0.5 * Math.exp(-i / 13) + 0.07) * lay.alpha, width: i === 0 ? 1.6 : Math.max(0.6, 1.25 - i * 0.02),
                 speed: (0.075 + 0.12 * Math.exp(-hh / 0.28)) * lay.L }
      })

      // static layer
      still = document.createElement('canvas')
      still.width = cv.width
      still.height = cv.height
      const s = still.getContext('2d')!
      s.scale(dpr, dpr)
      if (lay.car !== false) {
        drawFloor(s)
        drawReflection(s)
      }
      drawLines(s, 1)
      if (lay.car !== false) drawBody(s)

      parts.length = 0
      paths.forEach((_, i) => {
        if (i % 2 === 1 && i > 6) return
        for (let k = 0; k < lay.particles; k++) parts.push({ line: i, p: Math.random(), len: 40 + Math.random() * 90 })
      })
    }

    const drawFloor = (s: CanvasRenderingContext2D) => {
      const cx = toX(0.5)
      const g = s.createRadialGradient(cx, lay.gy, 0, cx, lay.gy, lay.L * 0.8)
      g.addColorStop(0, rgba(TURQ, 0.16 * lay.alpha))
      g.addColorStop(0.5, rgba(BLUE, 0.06 * lay.alpha))
      g.addColorStop(1, rgba(BLUE, 0))
      s.save()
      s.translate(cx, lay.gy)
      s.scale(1, 0.12)
      s.translate(-cx, -lay.gy)
      s.fillStyle = g
      s.beginPath()
      s.arc(cx, lay.gy, lay.L * 0.8, 0, Math.PI * 2)
      s.fill()
      s.restore()
      // horizon line of the studio floor
      const line = s.createLinearGradient(0, 0, w, 0)
      line.addColorStop(0, 'rgba(255,255,255,0)')
      line.addColorStop(0.5, `rgba(255,255,255,${0.1 * lay.alpha})`)
      line.addColorStop(1, 'rgba(255,255,255,0)')
      s.fillStyle = line
      s.fillRect(0, lay.gy, w, 1)
    }

    const strokePath = (s: CanvasRenderingContext2D, y: Float32Array, from = 0, to = y.length - 1) => {
      s.beginPath()
      s.moveTo(from * STEP, y[from])
      for (let k = from + 1; k <= to; k++) s.lineTo(k * STEP, y[k])
    }

    const lineGradient = (s: CanvasRenderingContext2D, a: number) => {
      const g = s.createLinearGradient(0, 0, w, 0)
      for (let t = 0; t <= 1.0001; t += 0.05) g.addColorStop(Math.min(1, t), rgba(airColor(t * w, lay), a * fadeAt(t * w, lay)))
      return g
    }

    const drawLines = (s: CanvasRenderingContext2D, k: number) => {
      s.save()
      s.lineCap = 'round'
      paths.forEach((p, i) => {
        s.strokeStyle = lineGradient(s, p.a * k)
        s.lineWidth = p.width
        if (i === 0) { s.shadowColor = rgba(TURQ, 0.6); s.shadowBlur = 14 } else s.shadowBlur = 0
        strokePath(s, p.y)
        s.stroke()
      })
      s.restore()
    }

    const outline = (s: CanvasRenderingContext2D, pts: [number, number][], mirror = false) => {
      s.beginPath()
      pts.forEach(([u, y], i) => {
        const X = toX(u), Y = mirror ? lay.gy + y * lay.L : toY(y)
        if (i === 0) s.moveTo(X, Y)
        else s.lineTo(X, Y)
      })
      s.closePath()
    }

    const drawBody = (s: CanvasRenderingContext2D) => {
      const body = bodyOutline()
      // paint
      const g = s.createLinearGradient(0, toY(0.34), 0, toY(0.06))
      g.addColorStop(0, '#1b2531')
      g.addColorStop(0.55, '#0f151d')
      g.addColorStop(1, '#090c11')
      s.fillStyle = g
      outline(s, body)
      s.fill()
      // a long soft reflection along the flank (the studio's light box)
      const flank = s.createLinearGradient(toX(0.05), 0, toX(0.98), 0)
      flank.addColorStop(0, 'rgba(255,255,255,0)')
      flank.addColorStop(0.45, `rgba(158,216,239,${0.1 * lay.alpha})`)
      flank.addColorStop(1, 'rgba(255,255,255,0)')
      s.save()
      outline(s, body)
      s.clip()
      s.fillStyle = flank
      s.fillRect(toX(0), toY(0.19), lay.L, lay.L * 0.05)
      // shoulder crease
      s.strokeStyle = `rgba(238,242,246,${0.16 * lay.alpha})`
      s.lineWidth = 1
      s.beginPath()
      s.moveTo(toX(0.06), toY(0.168))
      s.bezierCurveTo(toX(0.4), toY(0.176), toX(0.7), toY(0.19), toX(0.985), toY(0.2))
      s.stroke()
      s.restore()
      // glass
      const glass = glassOutline()
      if (glass.length) {
        const gg = s.createLinearGradient(toX(0.35), toY(0.32), toX(0.8), toY(0.24))
        gg.addColorStop(0, '#0b1016')
        gg.addColorStop(0.42, '#16222e')
        gg.addColorStop(0.5, `rgba(158,216,239,${0.22 * lay.alpha})`)
        gg.addColorStop(0.58, '#141e29')
        gg.addColorStop(1, '#0a0e13')
        s.fillStyle = gg
        outline(s, glass)
        s.fill()
        // B-pillar
        s.fillStyle = '#080b10'
        s.fillRect(toX(0.597), toY(0.33), lay.L * 0.012, lay.L * 0.1)
      }
      // rim light along the roof
      s.save()
      s.lineCap = 'round'
      const rim = s.createLinearGradient(toX(0), 0, toX(1), 0)
      rim.addColorStop(0, 'rgba(255,255,255,0)')
      rim.addColorStop(0.5, `rgba(255,255,255,${0.75 * lay.alpha})`)
      rim.addColorStop(1, 'rgba(255,255,255,0.05)')
      s.strokeStyle = rim
      s.lineWidth = 1.2
      s.beginPath()
      TOP_CURVE.forEach(([u, y], i) => {
        if (i === 0) s.moveTo(toX(u), toY(y))
        else s.lineTo(toX(u), toY(y))
      })
      s.stroke()
      s.restore()
      // wheels
      WHEELS.forEach((wh) => {
        const cx = toX(wh.u), cy = toY(wh.r), R = wh.r * lay.L
        s.fillStyle = '#050709'
        s.beginPath(); s.arc(cx, cy, R, 0, Math.PI * 2); s.fill()
        const rimG = s.createRadialGradient(cx - R * 0.2, cy - R * 0.3, R * 0.1, cx, cy, R * 0.7)
        rimG.addColorStop(0, '#2a3542')
        rimG.addColorStop(1, '#0d1218')
        s.fillStyle = rimG
        s.beginPath(); s.arc(cx, cy, R * 0.66, 0, Math.PI * 2); s.fill()
        s.strokeStyle = rgba(TURQ, 0.55 * lay.alpha)
        s.lineWidth = 1.2
        s.beginPath(); s.arc(cx, cy, R * 0.66, Math.PI * 1.4, Math.PI * 1.95); s.stroke()
      })
      // lights: a turquoise light line at the nose (electric), the Strauss-red bar at the tail
      s.save()
      s.lineCap = 'round'
      s.shadowBlur = 18
      s.shadowColor = rgba(TURQ, 0.95)
      s.strokeStyle = 'rgba(214,247,255,0.95)'
      s.lineWidth = 2.2
      s.beginPath(); s.moveTo(toX(0.012), toY(0.168)); s.lineTo(toX(0.075), toY(0.184)); s.stroke()
      s.shadowColor = rgba(RED, 0.95)
      s.strokeStyle = 'rgba(255,120,104,0.95)'
      s.beginPath(); s.moveTo(toX(0.93), toY(0.222)); s.lineTo(toX(0.996), toY(0.212)); s.stroke()
      s.restore()
    }

    const drawReflection = (s: CanvasRenderingContext2D) => {
      const r = document.createElement('canvas')
      r.width = cv.width
      r.height = cv.height
      const c = r.getContext('2d')!
      c.scale(dpr, dpr)
      c.fillStyle = '#0d131a'
      outline(c, bodyOutline(), true)
      c.fill()
      c.save()
      c.globalAlpha = 0.5
      paths.slice(0, 10).forEach((p) => {
        c.strokeStyle = lineGradient(c, p.a)
        c.lineWidth = p.width
        c.beginPath()
        for (let k = 0; k < p.y.length; k++) {
          const Y = lay.gy + (lay.gy - p.y[k]) * 0.9
          if (k === 0) c.moveTo(0, Y)
          else c.lineTo(k * STEP, Y)
        }
        c.stroke()
      })
      c.restore()
      c.globalCompositeOperation = 'destination-in'
      const fade = c.createLinearGradient(0, lay.gy, 0, lay.gy + lay.L * 0.28)
      fade.addColorStop(0, 'rgba(0,0,0,0.55)')
      fade.addColorStop(1, 'rgba(0,0,0,0)')
      c.fillStyle = fade
      c.fillRect(0, lay.gy, w, lay.L * 0.3)
      s.drawImage(r, 0, 0, w, h)
    }

    const frame = (now: number) => {
      raf = 0
      if (!still) return
      const t = (now - start) / 1000
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      // entrance: the air paints the car from the nose backwards in the first second (intro and login only)
      const reveal = mode === 'band' || reduce ? 1 : Math.min(1, t / 1.4)
      const ease = 1 - Math.pow(1 - reveal, 3)
      if (ease < 1) {
        ctx.save()
        ctx.beginPath()
        ctx.rect(w - w * ease - 40, 0, w * ease + 40, h)
        ctx.clip()
        ctx.globalAlpha = 0.25 + 0.75 * ease
        ctx.drawImage(still, 0, 0, w, h)
        ctx.restore()
      } else {
        ctx.drawImage(still, 0, 0, w, h)
      }
      if (!reduce) {
        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        ctx.lineCap = 'round'
        for (const q of parts) {
          const p = paths[q.line]
          q.p = (q.p + (p.speed / w) * 0.016) % 1
          const headX = w + q.len / 2 - q.p * (w + q.len)
          const lo = Math.max(0, Math.floor(headX / STEP))
          const hi = Math.min(p.y.length - 1, Math.floor((headX + q.len) / STEP))
          if (hi - lo < 2 || headX + q.len < w - w * ease - 40) continue
          const c = airColor(headX, lay)
          const g = ctx.createLinearGradient(headX + q.len, 0, headX, 0)
          g.addColorStop(0, rgba(c, 0))
          g.addColorStop(1, rgba(c, Math.min(0.95, p.a * 2.4) * fadeAt(headX, lay)))
          ctx.strokeStyle = g
          ctx.lineWidth = p.width + 0.6
          strokePath(ctx, p.y, lo, hi)
          ctx.stroke()
        }
        // a light sweep along the body every few seconds
        const cycle = (t % 7) / 1.8
        if (cycle < 1 && reveal >= 1 && lay.car !== false) {
          const p = paths[0]
          const uHead = -0.05 + cycle * 1.15
          const head = Math.round(Math.min(p.y.length - 1, Math.max(0, toX(uHead) / STEP)))
          const tail = Math.min(p.y.length - 1, head + Math.round((lay.L * 0.22) / STEP))
          if (tail - head > 2) {
            const g = ctx.createLinearGradient(tail * STEP, 0, head * STEP, 0)
            g.addColorStop(0, rgba(LEAF, 0))
            g.addColorStop(1, 'rgba(255,255,255,0.9)')
            ctx.strokeStyle = g
            ctx.lineWidth = 2.2
            strokePath(ctx, p.y, head, tail)
            ctx.stroke()
          }
        }
        ctx.restore()
      }
      if (running && !reduce) raf = requestAnimationFrame(frame)
    }

    const play = () => {
      if (running || !visible || document.hidden) return
      running = true
      if (!raf) raf = requestAnimationFrame(frame)
    }
    const pause = () => {
      running = false
      if (raf) cancelAnimationFrame(raf)
      raf = 0
    }

    build()
    start = performance.now()
    if (reduce) frame(start + 5000)
    else play()

    const ro = new ResizeObserver(() => {
      build()
      if (reduce || !running) frame(performance.now() + 5000)
    })
    ro.observe(box)
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting
      if (visible && !reduce) play()
      else pause()
    })
    io.observe(box)
    const onVis = () => (document.hidden ? pause() : !reduce && play())
    document.addEventListener('visibilitychange', onVis)

    // a slow camera: the artwork turns a little towards the pointer (fine pointers only)
    const fine = window.matchMedia?.('(pointer: fine)').matches
    let tx = 0, ty = 0, cx2 = 0, cy2 = 0, cam = 0
    const onMove = (e: PointerEvent) => {
      tx = (e.clientX / window.innerWidth - 0.5) * 2
      ty = (e.clientY / window.innerHeight - 0.5) * 2
      if (!cam) cam = requestAnimationFrame(moveCam)
    }
    const moveCam = () => {
      cam = 0
      cx2 += (tx - cx2) * 0.06
      cy2 += (ty - cy2) * 0.06
      box.style.setProperty('--rx', `${(-cy2 * 2.2).toFixed(2)}deg`)
      box.style.setProperty('--ry', `${(cx2 * 3.4).toFixed(2)}deg`)
      if (Math.abs(tx - cx2) > 0.002 || Math.abs(ty - cy2) > 0.002) cam = requestAnimationFrame(moveCam)
    }
    if (fine && !reduce && mode !== 'band') window.addEventListener('pointermove', onMove, { passive: true })

    return () => {
      pause()
      ro.disconnect()
      io.disconnect()
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('pointermove', onMove)
      if (cam) cancelAnimationFrame(cam)
    }
  }, [mode, reduce])

  return (
    <div ref={holder} className={`flowcar flowcar-${mode}`} aria-hidden="true">
      <canvas ref={canvas} />
    </div>
  )
}
