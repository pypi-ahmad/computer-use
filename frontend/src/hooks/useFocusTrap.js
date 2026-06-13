import { useEffect, useRef } from 'react'

const TABBABLE = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

/**
 * Accessible modal focus management (U2): initial focus, Tab/Shift+Tab trap,
 * Escape handler, background `inert`, and focus restoration on close.
 *
 * @param {boolean} active  Whether the modal is open.
 * @param {{ onEscape?: () => void, initialFocusRef?: React.RefObject<HTMLElement>, containerRef: React.RefObject<HTMLElement> }} opts
 */
export default function useFocusTrap(active, { onEscape, initialFocusRef, containerRef } = {}) {
  const previouslyFocused = useRef(null)

  useEffect(() => {
    if (!active) return undefined
    const container = containerRef?.current
    if (!container) return undefined

    previouslyFocused.current = document.activeElement

    // Make the rest of the app inert / hidden from assistive tech while open.
    const appRoot = document.getElementById('root')
    const hadInert = appRoot ? appRoot.hasAttribute('inert') : false
    if (appRoot && !container.contains(appRoot)) {
      try {
        appRoot.inert = true
      } catch {
        appRoot.setAttribute('aria-hidden', 'true')
      }
    }

    // Initial focus: prefer the supplied element, else the first tabbable.
    const focusInitial = () => {
      const target =
        initialFocusRef?.current || container.querySelector(TABBABLE) || container
      try {
        target.focus()
      } catch {
        /* element may not be focusable */
      }
    }
    focusInitial()

    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onEscape?.()
        return
      }
      if (e.key !== 'Tab') return
      const tabbables = Array.from(container.querySelectorAll(TABBABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      )
      if (tabbables.length === 0) {
        e.preventDefault()
        return
      }
      const first = tabbables[0]
      const last = tabbables[tabbables.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    container.addEventListener('keydown', onKeyDown)
    return () => {
      container.removeEventListener('keydown', onKeyDown)
      if (appRoot && !hadInert) {
        try {
          appRoot.inert = false
        } catch {
          appRoot.removeAttribute('aria-hidden')
        }
      }
      // Restore focus to the trigger if it's still in the document.
      const prev = previouslyFocused.current
      if (prev && typeof prev.focus === 'function' && document.contains(prev)) {
        prev.focus()
      }
    }
  }, [active, containerRef, initialFocusRef, onEscape])
}
