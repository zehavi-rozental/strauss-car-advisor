// The opening artwork: a pearl car on the hero blue, leaving a light trail in the seven colours of the Strauss
// burst. The car faces the words (screen right), so the trail flows into the empty side and never crosses the text;
// it grows out from behind the car, sways very slowly and fades out as it leaves the frame. Every few seconds one
// quiet shimmer runs along it. Minimal on purpose: one car, seven ribbons, nothing else.
// An imaginary car (never a real or catalog model); decorative only: aria-hidden, no text.
// The floor and the car are drawn once per size; only the ribbons move. It pauses when hidden or off screen,
// and draws one still frame when motion is reduced.
import { useEffect, useRef } from 'react'
import { TOP_CURVE, WHEELS, bodyOutline, glassOutline } from './aero'
import { useReducedMotion } from './motion'
import { hex, readPalette, rgba } from './palette'
import type { ArtPalette, RGB } from './palette'

export type FlowMode = 'intro' | 'band' | 'login'

interface Layout {
  x0: number; L: number; gy: number; width: number; alpha: number
  /** how far the trail runs behind the car, in car lengths */
  reach: number
}

function layoutFor(mode: FlowMode, w: number, h: number): Layout {
  if (mode === 'band') {
    const L = Math.max(170, Math.min(h * 2.2, w * 0.24, 320))
    const x0 = Math.max(90, w * 0.1)
    return { x0, L, gy: h * 0.86, width: 2.2, alpha: 0.95, reach: (x0 - 10) / L }
  }
  if (mode === 'login') {
    const L = Math.min(Math.max(w * 0.42, 320), 700)
    const x0 = w * 0.3
    return { x0, L, gy: h * 0.86, width: 3, alpha: 0.6, reach: (x0 - 10) / L }
  }
  if (w >= 900) {
    const L = Math.min(Math.max(w * 0.37, 360), 600)
    const x0 = w * 0.13
    return { x0, L, gy: h * 0.69, width: 3.2, alpha: 1, reach: (x0 - 8) / L }
  }
  const L = Math.min(w * 0.66, 440)
  const x0 = w - L - w * 0.08
  return { x0, L, gy: Math.min(h * 0.78, L * 0.5 + 60), width: 2.6, alpha: 1, reach: (x0 - 6) / L }
}

const easeOut = (x: number) => 1 - Math.pow(1 - x, 2.2)

export function FlowCar({ mode }: { mode: FlowMode }) {
  const holder = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const reduce = useReducedMotion()

  useEffect(() => {
    const box = holder.current, cv = canvas.current
    if (!box || !cv) return
    const ctx = cv.getContext('2d')
    if (!ctx) return
    let w = 0, h = 0, dpr = 1
    let lay = layoutFor(mode, 1, 1)
    let under: HTMLCanvasElement | null = null
    let over: HTMLCanvasElement | null = null
    const STEP = 3
    let ribbons: { y: Float32Array; env: Float32Array; alpha: Float32Array; from: number; to: number; color: RGB }[] = []
    let pal: ArtPalette = readPalette(box)
    let raf = 0, visible = true, running = false
    const start = performance.now()

    // the car faces the words (screen right): u = 0 at the nose, the tail at x0
    const toX = (u: number) => lay.x0 + (1 - u) * lay.L
    const toY = (yCar: number) => lay.gy - yCar * lay.L

    const layer = () => {
      const c = document.createElement('canvas')
      c.width = cv.width
      c.height = cv.height
      const g = c.getContext('2d')!
      g.scale(dpr, dpr)
      return [c, g] as const
    }

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
      pal = readPalette(box)

      // seven ribbons leave the rear of the car, stacked from the roof line to the bumper, and fan out a little
      const tail = toX(1)
      const startX = tail + lay.L * 0.16                  // starts hidden behind the body
      const run = Math.max(1, lay.L * lay.reach)
      ribbons = pal.ribbons.map((color, i) => {
        const y0 = toY(0.245 - i * 0.021)
        const y1 = y0 + lay.L * (i - 3) * 0.028 + lay.L * 0.02
        const n = Math.ceil(w / STEP) + 1
        const y = new Float32Array(n), env = new Float32Array(n), alpha = new Float32Array(n)
        const from = Math.max(0, Math.floor((tail - run) / STEP))     // the far end
        const to = Math.min(n - 1, Math.ceil(startX / STEP))         // at the car
        for (let j = from; j <= to; j++) {
          const d = Math.min(1, Math.max(0, (tail - j * STEP) / run))
          y[j] = y0 + (y1 - y0) * easeOut(d)
          env[j] = d
          alpha[j] = d < 0.3 ? 1 : Math.max(0, 1 - (d - 0.3) / 0.7) ** 1.6     // a long, soft fade: never cut
        }
        return { y, env, alpha, from, to, color }
      })

      const [u, ug] = layer()
      drawFloor(ug)
      drawReflection(ug)
      under = u
      const [o, og] = layer()
      drawCar(og)
      over = o
    }

    const drawFloor = (s: CanvasRenderingContext2D) => {
      const cx = toX(0.5)
      const ell = (rx: number, ry: number, a: number, y = lay.gy) => {
        const g = s.createRadialGradient(cx, y, 0, cx, y, rx)
        g.addColorStop(0, rgba(pal.shadow, a))
        g.addColorStop(1, rgba(pal.shadow, 0))
        s.save()
        s.translate(cx, y)
        s.scale(1, ry / rx)
        s.translate(-cx, -y)
        s.fillStyle = g
        s.beginPath()
        s.arc(cx, y, rx, 0, Math.PI * 2)
        s.fill()
        s.restore()
      }
      ell(lay.L * 0.66, lay.L * 0.055, 0.32 * lay.alpha)
      ell(lay.L * 0.46, lay.L * 0.018, 0.55 * lay.alpha, lay.gy + 1)
      WHEELS.forEach((wh) => {
        const x = toX(wh.u)
        const g = s.createRadialGradient(x, lay.gy, 0, x, lay.gy, wh.r * lay.L * 1.2)
        g.addColorStop(0, rgba(pal.shadow, 0.6 * lay.alpha))
        g.addColorStop(1, rgba(pal.shadow, 0))
        s.fillStyle = g
        s.fillRect(x - wh.r * lay.L * 1.3, lay.gy - 6, wh.r * lay.L * 2.6, 12)
      })
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

    const drawReflection = (s: CanvasRenderingContext2D) => {
      const [r, c] = layer()
      const paint = c.createLinearGradient(0, lay.gy, 0, lay.gy + lay.L * 0.3)
      paint.addColorStop(0, hex(pal.reflection[0]))
      paint.addColorStop(1, hex(pal.reflection[1]))
      c.fillStyle = paint
      outline(c, bodyOutline(), true)
      c.fill()
      c.globalCompositeOperation = 'destination-in'
      const fade = c.createLinearGradient(0, lay.gy, 0, lay.gy + lay.L * 0.22)
      fade.addColorStop(0, 'rgba(0,0,0,0.16)')
      fade.addColorStop(1, 'rgba(0,0,0,0)')
      c.fillStyle = fade
      c.fillRect(0, lay.gy, w, lay.L * 0.3)
      s.drawImage(r, 0, 0, w, h)
    }

    const drawCar = (s: CanvasRenderingContext2D) => {
      const body = bodyOutline()
      const top = toY(0.335), bottom = toY(0.06)
      // pearl paint: bright roof, a soft shoulder highlight, the flank turning away into a cool shadow
      const paint = s.createLinearGradient(0, top, 0, bottom)
      ;[0, 0.32, 0.5, 0.62, 0.8, 1].forEach((at, k) => paint.addColorStop(at, hex(pal.paint[k])))
      s.fillStyle = paint
      outline(s, body)
      s.fill()
      s.save()
      outline(s, body)
      s.clip()
      const lightbox = s.createLinearGradient(toX(1), 0, toX(0), 0)
      lightbox.addColorStop(0, rgba(pal.highlight, 0))
      lightbox.addColorStop(0.35, rgba(pal.highlight, 0.85))
      lightbox.addColorStop(0.75, rgba(pal.highlight, 0.4))
      lightbox.addColorStop(1, rgba(pal.highlight, 0))
      s.fillStyle = lightbox
      s.fillRect(toX(1), toY(0.205), lay.L, lay.L * 0.022)
      const sky = s.createLinearGradient(0, toY(0.14), 0, bottom)
      sky.addColorStop(0, rgba(pal.skyTint, 0))
      sky.addColorStop(1, rgba(pal.skyTint, 0.35))
      s.fillStyle = sky
      s.fillRect(toX(1), toY(0.14), lay.L, lay.L * 0.1)
      s.lineCap = 'round'
      s.strokeStyle = rgba(pal.line, 0.35)
      s.lineWidth = 1
      s.beginPath()
      s.moveTo(toX(0.05), toY(0.17))
      s.bezierCurveTo(toX(0.4), toY(0.178), toX(0.7), toY(0.192), toX(0.985), toY(0.2))
      s.stroke()
      s.strokeStyle = rgba(pal.line, 0.34)
      s.beginPath()
      s.moveTo(toX(0.27), toY(0.1))
      s.lineTo(toX(0.72), toY(0.1))
      s.stroke()
      s.strokeStyle = rgba(pal.line, 0.32)
      s.beginPath()
      s.moveTo(toX(0.592), toY(0.226)); s.lineTo(toX(0.588), toY(0.09))
      s.stroke()
      s.strokeStyle = rgba(pal.line, 0.6)
      s.lineWidth = 2
      ;[0.5, 0.69].forEach((u) => { s.beginPath(); s.moveTo(toX(u + 0.04), toY(0.19)); s.lineTo(toX(u), toY(0.188)); s.stroke() })
      s.restore()
      s.strokeStyle = rgba(pal.line, 0.55)
      s.lineWidth = 1
      outline(s, body)
      s.stroke()
      const glass = glassOutline()
      if (glass.length) {
        const gg = s.createLinearGradient(toX(0.82), toY(0.31), toX(0.36), toY(0.23))
        ;[0, 0.4, 0.47, 0.55, 1].forEach((at, k) => gg.addColorStop(at, hex(pal.glass[k])))
        s.fillStyle = gg
        outline(s, glass)
        s.fill()
        s.strokeStyle = rgba(pal.rim[2], 0.9)
        s.lineWidth = 1.4
        s.stroke()
        s.fillStyle = hex(pal.pillar)
        s.fillRect(toX(0.597), toY(0.33), lay.L * 0.012, lay.L * 0.11)
      }
      s.save()
      s.lineCap = 'round'
      const rim = s.createLinearGradient(toX(0), 0, toX(1), 0)
      rim.addColorStop(0, rgba(pal.highlight, 0))
      rim.addColorStop(0.5, rgba(pal.highlight, 1))
      rim.addColorStop(1, rgba(pal.highlight, 0))
      s.strokeStyle = rim
      s.lineWidth = 2
      s.beginPath()
      TOP_CURVE.forEach(([u, y], i) => {
        if (i === 0) s.moveTo(toX(u), toY(y) + 1.5)
        else s.lineTo(toX(u), toY(y) + 1.5)
      })
      s.stroke()
      s.restore()
      WHEELS.forEach((wh) => {
        const cx = toX(wh.u), cy = toY(wh.r), R = wh.r * lay.L
        s.fillStyle = hex(pal.tyre)
        s.beginPath(); s.arc(cx, cy, R, 0, Math.PI * 2); s.fill()
        const rimG = s.createRadialGradient(cx - R * 0.25, cy - R * 0.3, R * 0.05, cx, cy, R * 0.7)
        ;[0, 0.6, 1].forEach((at, k) => rimG.addColorStop(at, hex(pal.rim[k])))
        s.fillStyle = rimG
        s.beginPath(); s.arc(cx, cy, R * 0.68, 0, Math.PI * 2); s.fill()
        s.fillStyle = hex(pal.rimInner)
        s.beginPath(); s.arc(cx, cy, R * 0.6, 0, Math.PI * 2); s.fill()
        s.strokeStyle = hex(pal.spoke)
        s.lineWidth = Math.max(1.4, R * 0.07)
        for (let k = 0; k < 10; k++) {
          const a = (k / 10) * Math.PI * 2
          s.beginPath()
          s.moveTo(cx + Math.cos(a) * R * 0.14, cy + Math.sin(a) * R * 0.14)
          s.lineTo(cx + Math.cos(a + 0.12) * R * 0.6, cy + Math.sin(a + 0.12) * R * 0.6)
          s.stroke()
        }
        s.fillStyle = hex(pal.hub)
        s.beginPath(); s.arc(cx, cy, R * 0.15, 0, Math.PI * 2); s.fill()
        s.fillStyle = hex(pal.hubDot)
        s.beginPath(); s.arc(cx, cy, R * 0.05, 0, Math.PI * 2); s.fill()
      })
      s.save()
      s.lineCap = 'round'
      s.lineWidth = 2.6
      s.shadowBlur = 10
      s.shadowColor = rgba(pal.headLight, 0.8)
      s.strokeStyle = hex(pal.headLight)
      s.beginPath(); s.moveTo(toX(0.012), toY(0.168)); s.lineTo(toX(0.08), toY(0.185)); s.stroke()
      s.shadowColor = rgba(pal.tailLight, 0.7)
      s.strokeStyle = hex(pal.tailLight)
      s.beginPath(); s.moveTo(toX(0.93), toY(0.222)); s.lineTo(toX(0.996), toY(0.212)); s.stroke()
      s.restore()
    }

    const ribbonY = (r: (typeof ribbons)[number], i: number, j: number, t: number) => {
      if (reduce) return r.y[j]
      // a very slow sway, growing with the distance from the car (one wave every ~7 seconds)
      const x = j * STEP
      return r.y[j] + Math.sin((x / (lay.L * 0.6)) * Math.PI * 2 - t * 0.9 + i * 0.4) * lay.L * 0.014 * r.env[j]
    }

    const drawRibbons = (t: number, grow: number) => {
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ribbons.forEach((r, i) => {
        // the trail grows from the car (r.to) towards the far end (r.from)
        const from = Math.max(r.from, r.to - Math.round((r.to - r.from) * grow))
        if (r.to - from < 2) return
        // the soft fade is a gradient along x: gone at the far end, full near the car
        const g = ctx.createLinearGradient(r.from * STEP, 0, r.to * STEP, 0)
        for (let k = 0; k <= 10; k++) {
          const j = Math.round(r.from + ((r.to - r.from) * k) / 10)
          g.addColorStop(k / 10, rgba(r.color, 0.95 * lay.alpha * r.alpha[j]))
        }
        ctx.strokeStyle = g
        ctx.lineWidth = lay.width
        ctx.beginPath()
        for (let j = from; j <= r.to; j++) {
          const y = ribbonY(r, i, j, t)
          if (j === from) ctx.moveTo(j * STEP, y)
          else ctx.lineTo(j * STEP, y)
        }
        ctx.stroke()
      })
      if (reduce || grow < 1) return
      // one quiet shimmer every few seconds, from the car outwards
      const cycle = (t % 5) / 2.2
      if (cycle >= 1) return
      ctx.save()
      ribbons.forEach((r, i) => {
        const span = r.to - r.from
        const head = r.to - Math.round(span * (cycle * 1.1 - i * 0.015))
        const lo = Math.max(r.from, head), hi = Math.min(r.to, head + Math.round(span * 0.18))
        if (hi - lo < 3) return
        const g = ctx.createLinearGradient(hi * STEP, 0, lo * STEP, 0)
        const a = 0.75 * r.alpha[lo]
        g.addColorStop(0, rgba(pal.highlight, 0))
        g.addColorStop(1, rgba(pal.highlight, a))
        ctx.strokeStyle = g
        ctx.lineWidth = Math.max(1.2, lay.width - 1.4)
        ctx.beginPath()
        for (let j = lo; j <= hi; j++) {
          const y = ribbonY(r, i, j, t)
          if (j === lo) ctx.moveTo(j * STEP, y)
          else ctx.lineTo(j * STEP, y)
        }
        ctx.stroke()
      })
      ctx.restore()
    }

    const frame = (now: number) => {
      raf = 0
      if (!under) return
      const t = (now - start) / 1000
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      // entrance: the car settles, then the trail grows out from behind it (once)
      const settle = mode === 'band' || reduce ? 1 : Math.min(1, t / 0.7)
      const grow = mode === 'band' || reduce ? 1 : easeOut(Math.min(1, Math.max(0, (t - 0.35) / 1.4)))
      ctx.drawImage(under, 0, 0, w, h)
      drawRibbons(t, grow)
      if (over) {
        ctx.save()
        if (settle < 1) {
          ctx.globalAlpha = easeOut(settle)
          ctx.translate(-(1 - easeOut(settle)) * 18, 0)
        }
        ctx.drawImage(over, 0, 0, w, h)
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

    return () => {
      pause()
      ro.disconnect()
      io.disconnect()
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [mode, reduce])

  return (
    <div ref={holder} className={`flowcar flowcar-${mode}`} aria-hidden="true">
      <canvas ref={canvas} />
    </div>
  )
}
