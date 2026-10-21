// The "reduce motion" preference: the system setting, overridden by the user's choice in the menu
// (stored per browser, never sent anywhere). <html data-motion="reduce"> drives the CSS.
import { useEffect, useState } from 'react'

const KEY = 'sca.motion'
const EVENT = 'sca:motion'

function stored(): 'reduce' | 'full' | null {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'reduce' || v === 'full' ? v : null
  } catch {
    return null
  }
}

function systemReduce(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
}

export function reducedMotion(): boolean {
  const s = stored()
  return s ? s === 'reduce' : systemReduce()
}

export function applyMotion(): void {
  if (typeof document === 'undefined') return
  if (reducedMotion()) document.documentElement.dataset.motion = 'reduce'
  else delete document.documentElement.dataset.motion
}

export function setReducedMotion(reduce: boolean): void {
  try {
    localStorage.setItem(KEY, reduce ? 'reduce' : 'full')
  } catch {
    // private mode: the choice lasts for this page only
  }
  applyMotion()
  window.dispatchEvent(new Event(EVENT))
}

export function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(reducedMotion)
  useEffect(() => {
    const update = () => setReduce(reducedMotion())
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    mq?.addEventListener?.('change', update)
    window.addEventListener(EVENT, update)
    return () => {
      mq?.removeEventListener?.('change', update)
      window.removeEventListener(EVENT, update)
    }
  }, [])
  return reduce
}
