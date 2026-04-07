import { Component } from 'react'

/**
 * React error boundary — catches unhandled exceptions in the component tree
 * and renders a recovery UI instead of a blank page.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          height: '100vh', background: 'var(--bg-primary, #0f1117)', color: 'var(--text-primary, #e4e6ed)',
          fontFamily: 'Inter, -apple-system, sans-serif', gap: 16, padding: 24, textAlign: 'center',
        }}>
          <h1 style={{ fontSize: 20, fontWeight: 700 }}>Something went wrong</h1>
          <p style={{ fontSize: 14, color: 'var(--text-secondary, #9499ad)', maxWidth: 400 }}>
            An unexpected error occurred. Please reload the page to continue.
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '10px 24px', fontSize: 14, fontWeight: 600, border: 'none', borderRadius: 8,
              background: 'var(--accent, #6c63ff)', color: '#fff', cursor: 'pointer',
            }}
          >
            Reload Page
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
