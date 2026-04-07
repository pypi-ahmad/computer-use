import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      height: '100vh', background: 'var(--bg-primary)', color: 'var(--text-primary)',
      fontFamily: 'var(--font-sans)', gap: 16, textAlign: 'center',
    }}>
      <h1 style={{ fontSize: 48, fontWeight: 700, color: 'var(--accent)' }}>404</h1>
      <p style={{ fontSize: 16, color: 'var(--text-secondary)' }}>Page not found</p>
      <Link to="/" style={{
        padding: '10px 24px', fontSize: 14, fontWeight: 600, borderRadius: 8,
        background: 'var(--accent)', color: '#fff', textDecoration: 'none',
      }}>
        Go to Dashboard
      </Link>
    </div>
  )
}
