import { render } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import CompletionBanner from './CompletionBanner'

describe('CompletionBanner', () => {
  it('renders nothing when finishData is null', () => {
    const { container } = render(<CompletionBanner finishData={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the correct label/class per status', () => {
    for (const [status, label, cls] of [
      ['completed', 'Task Complete', 'success'],
      ['error', 'Task Failed', 'error'],
      ['stopped', 'Task Stopped', 'stopped'],
    ]) {
      const { container, getByText, unmount } = render(
        <CompletionBanner finishData={{ status, steps: 2 }} />,
      )
      expect(getByText(label)).toBeInTheDocument()
      expect(container.querySelector('.completion-banner')).toHaveClass(cls)
      unmount()
    }
  })

  it('renders plain final_text without an iframe when no grounding', () => {
    const { container, getByText } = render(
      <CompletionBanner finishData={{ status: 'completed', final_text: 'all done' }} />,
    )
    expect(getByText('all done')).toBeInTheDocument()
    expect(container.querySelector('iframe')).toBeNull()
  })

  it('U1: provider grounding HTML renders in a fully-locked-down iframe', () => {
    const hostile = '<script>window.__pwned=1</script><a href="javascript:alert(1)">x</a>'
    const { container } = render(
      <CompletionBanner
        finishData={{
          status: 'completed',
          final_text: 'done',
          gemini_grounding: { renderedContent: hostile, webSearchQueries: [], groundingChunks: [] },
        }}
      />,
    )
    const iframe = container.querySelector('iframe')
    expect(iframe).not.toBeNull()
    // Security invariant: empty sandbox → no scripts, no same-origin, no popups,
    // no popup-escape. Provider HTML is inert.
    const sandbox = iframe.getAttribute('sandbox')
    expect(sandbox).toBe('')
    expect(sandbox).not.toContain('allow-scripts')
    expect(sandbox).not.toContain('allow-same-origin')
    expect(sandbox).not.toContain('allow-popups')
    expect(iframe.getAttribute('referrerpolicy')).toBe('no-referrer')
    // __pwned cannot execute (no allow-scripts) regardless of srcDoc bytes.
    expect(window.__pwned).toBeUndefined()
  })
})
