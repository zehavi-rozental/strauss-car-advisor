// Behaviour of a modal sheet (no style): focus the close button on open, Escape closes, the page behind does not
// scroll, and focus goes back to where it was when it closes.
import { useEffect, useRef } from 'react'

export function useDialog(onClose: () => void) {
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    document.body.classList.add('has-sheet')
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.classList.remove('has-sheet')
      before?.focus?.()
    }
  }, [onClose])
  return closeRef
}
