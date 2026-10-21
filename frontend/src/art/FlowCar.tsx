// The opening artwork: a pearl car in a soft studio, wrapped by moving ribbons in the colours of the Strauss
// burst. The ribbons follow the air over an imaginary body (never a real or catalog model), sway slowly and carry
// travelling light; the car sits on a reflective floor. Decorative only: aria-hidden, no text.
// Two still layers (floor, car) are drawn once per size; only the ribbons move. It pauses when hidden or off screen,
// and draws one still frame when motion is reduced.
import { useEffect, useRef } from 'react'
import { TOP_CURVE, WHEELS, bodyOutline, displacement, glassOutline, streamY } from './aero'
import { useReducedMotion } from './motion'

export type FlowMode = 'intro' | 'band' | 'login'

interface Layout {
  x0: number; L: number; gy: number; width: number; alpha: number
  car: boolean
  /** fade the ribbons on the text side: full until `from`, gone at `to` (screen x) */
  fade?: { from: number; to: number }
}

function layoutFor(mode: FlowMode, w: number, h: number): Layout {
  if (mode === 'band') {
    if (w < 720) return { x0: -w, L: w * 1.4, gy: h * 0.98, width: 1.6, alpha: 0.32, car: false }
    const L = Math.max(200, Math.min(h * 2.3, w * 0.3, 380))
    const x0 = Math.max(16, w * 0.04)
    return { x0, L, gy: h * 0.86, width: 2.2, alpha: 0.9, car: true, fade: { from: x0 + L * 1.02, to: x0 + L * 1.45 } }
  }
  if (mode === 'login') {
    const L = Math.min(Math.max(w * 0.46, 340), 760)
    return { x0: w / 2 - L / 2, L, gy: h * 0.84, width: 3, alpha: 0.65, car: true }
  }
  if (w >= 900) {
    const L = Math.min(Math.max(w * 0.4, 400), 660)
    const x0 = w * 0.27 - L / 2
    return { x0, L, gy: h * 0.7, width: 3.4, alpha: 1, car: true, fade: { from: x0 + L * 1.03, to: x0 + L * 1.28 } }
  }
  const L = Math.min(w * 0.86, 540)
  return { x0: (w - L) / 2, L, gy: Math.min(h * 0.78, L * 0.5 + 40), width: 2.6, alpha: 1, car: true }
}

// the burst, top ribbon to bottom ribbon
const RIBBONS = ['#ed3024', '#f48326', '#b5d36a', '#78a12f', '#9ed8ef', '#00bae2', '#0390cb']
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const rgba = (c: number[], a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`

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
    let base: { y: Float32Array; lock: Float32Array; color: number[]; thin: boolean; phase: number }[] = []
    let raf = 0, visible = true, running = false
    const start = performance.now()

    const toY = (yCar: number) => lay.gy - yCar * lay.L
    // the car faces the text (screen right): u = 0 at the nose
    const toX = (u: number) => lay.x0 + (1 - u) * lay.L
    const uAt = (x: number) => 1 - (x - lay.x0) / lay.L
    const fadeAt = (x: number) => {
      const f = lay.fade
      if (!f || x <= f.from) return 1
      if (x >= f.to) return 0
      return 1 - (x - f.from) / (f.to - f.from)
    }
    const edgeAt = (x: number) => Math.min(1, x / (w * 0.08))      // ribbons come in softly from the far edge

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

      const lift = lay.car ? displacement({ from: -3, to: 4, step: 0.004 }, 0.02, 0.13) : { at: () => 0 }
      // seven ribbons, a thin hairline between each pair; the lowest hugs the roof
      base = []
      const n = RIBBONS.length
      for (let i = 0; i < n * 2 - 1; i++) {
        const k = n - 1 - Math.floor(i / 2)                 // bottom ribbon (blue) is drawn closest to the car
        const thin = i % 2 === 1
        const hh = 0.012 + i * 0.022 * (1 + i * 0.02)
        const y = new Float32Array(Math.ceil(w / STEP) + 1)
        const lock = new Float32Array(y.length)
        for (let j = 0; j < y.length; j++) {
          const u = uAt(j * STEP)
          const l = lift.at(u)
          y[j] = toY(streamY(hh, l, 0.5))
          // near the body the ribbons hold still (they hug the car); away from it they sway
          lock[j] = lay.car ? Math.min(1, Math.abs(u - 0.5) > 0.75 ? 1 : Math.max(0.15, (Math.abs(u - 0.5) - 0.2) / 0.55)) : 1
        }
        base.push({ y, lock, color: rgb(RIBBONS[thin ? Math.min(n - 1, k) : k]), thin, phase: i * 0.6 })
      }

      const [u, ug] = layer()
      if (lay.car) {
        drawFloor(ug)
        drawReflection(ug)
      }
      under = u
      if (lay.car) {
        const [o, og] = layer()
        drawCar(og)
        over = o
      } else over = null
    }

    const drawFloor = (s: CanvasRenderingContext2D) => {
      // contact shadow and a soft ambient pool under the car
      const cx = toX(0.5)
      const ell = (rx: number, ry: number, a: number, y = lay.gy) => {
        const g = s.createRadialGradient(cx, y, 0, cx, y, rx)
        g.addColorStop(0, `rgba(2,16,30,${a})`)
        g.addColorStop(1, 'rgba(2,16,30,0)')
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
        g.addColorStop(0, `rgba(2,16,30,${0.6 * lay.alpha})`)
        g.addColorStop(1, 'rgba(2,16,30,0)')
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
      paint.addColorStop(0, '#a9c3d6')
      paint.addColorStop(1, '#6f97b4')
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
      paint.addColorStop(0, '#ffffff')
      paint.addColorStop(0.32, '#f4f6f8')
      paint.addColorStop(0.5, '#e3e8ed')
      paint.addColorStop(0.62, '#f2f4f7')
      paint.addColorStop(0.8, '#cdd5de')
      paint.addColorStop(1, '#a9b4c0')
      s.fillStyle = paint
      outline(s, body)
      s.fill()
      s.save()
      outline(s, body)
      s.clip()
      // the studio's light box running along the flank
      const box = s.createLinearGradient(toX(1), 0, toX(0), 0)
      box.addColorStop(0, 'rgba(255,255,255,0)')
      box.addColorStop(0.35, 'rgba(255,255,255,0.85)')
      box.addColorStop(0.75, 'rgba(255,255,255,0.4)')
      box.addColorStop(1, 'rgba(255,255,255,0)')
      s.fillStyle = box
      s.fillRect(toX(1), toY(0.205), lay.L, lay.L * 0.022)
      // a cool sky tint low on the doors, from the floor
      const sky = s.createLinearGradient(0, toY(0.14), 0, bottom)
      sky.addColorStop(0, 'rgba(158,216,239,0)')
      sky.addColorStop(1, 'rgba(158,216,239,0.35)')
      s.fillStyle = sky
      s.fillRect(toX(1), toY(0.14), lay.L, lay.L * 0.1)
      // shoulder and rocker lines
      s.lineCap = 'round'
      s.strokeStyle = 'rgba(120,136,152,0.35)'
      s.lineWidth = 1
      s.beginPath()
      s.moveTo(toX(0.05), toY(0.17))
      s.bezierCurveTo(toX(0.4), toY(0.178), toX(0.7), toY(0.192), toX(0.985), toY(0.2))
      s.stroke()
      s.strokeStyle = 'rgba(96,110,126,0.3)'
      s.beginPath()
      s.moveTo(toX(0.27), toY(0.1))
      s.lineTo(toX(0.72), toY(0.1))
      s.stroke()
      // door cut and handles
      s.strokeStyle = 'rgba(96,110,126,0.28)'
      s.beginPath()
      s.moveTo(toX(0.592), toY(0.226)); s.lineTo(toX(0.588), toY(0.09))
      s.stroke()
      s.strokeStyle = 'rgba(70,84,100,0.45)'
      s.lineWidth = 2
      ;[0.5, 0.69].forEach((u) => { s.beginPath(); s.moveTo(toX(u), toY(0.188)); s.lineTo(toX(u + 0.04), toY(0.19)); s.stroke() })
      s.restore()
      // outline, crisp
      s.strokeStyle = 'rgba(120,136,152,0.55)'
      s.lineWidth = 1
      outline(s, body)
      s.stroke()
      // glass: deep tint with the sky across it
      const glass = glassOutline()
      if (glass.length) {
        const gg = s.createLinearGradient(toX(0.82), toY(0.31), toX(0.36), toY(0.23))
        gg.addColorStop(0, '#1d2a39')
        gg.addColorStop(0.4, '#2c3e53')
        gg.addColorStop(0.47, '#7fa6c4')
        gg.addColorStop(0.55, '#33465c')
        gg.addColorStop(1, '#16212d')
        s.fillStyle = gg
        outline(s, glass)
        s.fill()
        s.strokeStyle = 'rgba(160,172,186,0.9)'
        s.lineWidth = 1.4
        s.stroke()
        s.fillStyle = '#18222e'
        s.fillRect(Math.min(toX(0.585), toX(0.597)), toY(0.33), lay.L * 0.012, lay.L * 0.11)
      }
      // roof highlight
      s.save()
      s.lineCap = 'round'
      const rim = s.createLinearGradient(toX(1), 0, toX(0), 0)
      rim.addColorStop(0, 'rgba(255,255,255,0)')
      rim.addColorStop(0.5, 'rgba(255,255,255,1)')
      rim.addColorStop(1, 'rgba(255,255,255,0)')
      s.strokeStyle = rim
      s.lineWidth = 2
      s.beginPath()
      TOP_CURVE.forEach(([u, y], i) => {
        if (i === 0) s.moveTo(toX(u), toY(y) + 1.5)
        else s.lineTo(toX(u), toY(y) + 1.5)
      })
      s.stroke()
      s.restore()
      // wheels: tyre, ten-spoke silver rim, hub
      WHEELS.forEach((wh) => {
        const cx = toX(wh.u), cy = toY(wh.r), R = wh.r * lay.L
        s.fillStyle = '#1c242d'
        s.beginPath(); s.arc(cx, cy, R, 0, Math.PI * 2); s.fill()
        s.strokeStyle = 'rgba(255,255,255,0.12)'
        s.lineWidth = 1
        s.beginPath(); s.arc(cx, cy, R * 0.93, Math.PI * 1.1, Math.PI * 1.7); s.stroke()
        const rimG = s.createRadialGradient(cx - R * 0.25, cy - R * 0.3, R * 0.05, cx, cy, R * 0.7)
        rimG.addColorStop(0, '#f6f8fa')
        rimG.addColorStop(0.6, '#c3ccd5')
        rimG.addColorStop(1, '#8794a1')
        s.fillStyle = rimG
        s.beginPath(); s.arc(cx, cy, R * 0.68, 0, Math.PI * 2); s.fill()
        s.fillStyle = '#5a6673'
        s.beginPath(); s.arc(cx, cy, R * 0.6, 0, Math.PI * 2); s.fill()
        s.strokeStyle = '#dde3e9'
        s.lineWidth = Math.max(1.4, R * 0.07)
        for (let k = 0; k < 10; k++) {
          const a = (k / 10) * Math.PI * 2
          s.beginPath()
          s.moveTo(cx + Math.cos(a) * R * 0.14, cy + Math.sin(a) * R * 0.14)
          s.lineTo(cx + Math.cos(a + 0.12) * R * 0.6, cy + Math.sin(a + 0.12) * R * 0.6)
          s.stroke()
        }
        s.fillStyle = '#eef2f5'
        s.beginPath(); s.arc(cx, cy, R * 0.15, 0, Math.PI * 2); s.fill()
        s.fillStyle = '#ed3024'
        s.beginPath(); s.arc(cx, cy, R * 0.05, 0, Math.PI * 2); s.fill()
      })
      // lights: a turquoise line at the nose, the Strauss red at the tail
      s.save()
      s.lineCap = 'round'
      s.lineWidth = 2.6
      s.shadowBlur = 10
      s.shadowColor = 'rgba(0,186,226,0.8)'
      s.strokeStyle = '#3ccbea'
      s.beginPath(); s.moveTo(toX(0.012), toY(0.168)); s.lineTo(toX(0.08), toY(0.185)); s.stroke()
      s.shadowColor = 'rgba(237,48,36,0.7)'
      s.strokeStyle = '#ed3024'
      s.beginPath(); s.moveTo(toX(0.93), toY(0.222)); s.lineTo(toX(0.996), toY(0.212)); s.stroke()
      s.restore()
    }

    const ribbonY = (r: (typeof base)[number], j: number, t: number) => {
      if (reduce) return r.y[j]
      const x = j * STEP
      const sway = Math.sin((x / (lay.L * 0.55)) * Math.PI * 2 + r.phase - t * 0.9) * lay.L * 0.012
      return r.y[j] + sway * r.lock[j]
    }

    const drawRibbons = (t: number) => {
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      for (let i = base.length - 1; i >= 0; i--) {
        const r = base[i]
        const g = ctx.createLinearGradient(0, 0, w, 0)
        for (let s = 0; s <= 1.0001; s += 0.05) g.addColorStop(Math.min(1, s), rgba(r.color, (r.thin ? 0.35 : 0.92) * lay.alpha * fadeAt(s * w) * edgeAt(s * w)))
        ctx.strokeStyle = g
        ctx.lineWidth = r.thin ? 1 : lay.width
        ctx.beginPath()
        for (let j = 0; j < r.y.length; j++) {
          const y = ribbonY(r, j, t)
          if (j === 0) ctx.moveTo(0, y)
          else ctx.lineTo(j * STEP, y)
        }
        ctx.stroke()
      }
      if (reduce) return
      // travelling light: one bright run per ribbon, from the nose side towards the tail
      ctx.save()
      for (let i = 0; i < base.length; i += 2) {
        const r = base[i]
        const span = w + lay.L * 0.6
        const head = w - (((t * lay.L * 0.32 + i * 97) % span) - lay.L * 0.3)
        const len = lay.L * 0.18
        const lo = Math.max(0, Math.floor(head / STEP)), hi = Math.min(r.y.length - 1, Math.floor((head + len) / STEP))
        if (hi - lo < 3) continue
        const g = ctx.createLinearGradient(head + len, 0, head, 0)
        const a = fadeAt(head) * edgeAt(head)
        g.addColorStop(0, 'rgba(255,255,255,0)')
        g.addColorStop(0.7, `rgba(255,255,255,${0.75 * a})`)
        g.addColorStop(1, `rgba(255,255,255,${0.95 * a})`)
        ctx.strokeStyle = g
        ctx.lineWidth = Math.max(1.2, lay.width - 1.2)
        ctx.beginPath()
        for (let j = lo; j <= hi; j++) {
          const y = ribbonY(r, j, t)
          if (j === lo) ctx.moveTo(j * STEP, y)
          else ctx.lineTo(j * STEP, y)
        }
        ctx.stroke()
      }
      ctx.restore()
    }

    const frame = (now: number) => {
      raf = 0
      if (!under) return
      const t = (now - start) / 1000
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      // entrance: the ribbons draw in from the nose side, then the car settles
      const reveal = mode === 'band' || reduce ? 1 : Math.min(1, t / 1.3)
      const ease = 1 - Math.pow(1 - reveal, 3)
      ctx.drawImage(under, 0, 0, w, h)
      ctx.save()
      if (ease < 1) {
        ctx.beginPath()
        ctx.rect(w - w * ease - 40, 0, w * ease + 40, h)
        ctx.clip()
      }
      drawRibbons(t)
      ctx.restore()
      if (over) {
        ctx.save()
        if (ease < 1) {
          ctx.globalAlpha = Math.min(1, ease * 1.4)
          ctx.translate(0, (1 - ease) * 10)
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
      box.style.setProperty('--rx', `${(-cy2 * 1.6).toFixed(2)}deg`)
      box.style.setProperty('--ry', `${(cx2 * 2.6).toFixed(2)}deg`)
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
