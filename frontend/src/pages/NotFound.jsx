import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <main style={{
      minHeight: '100vh',
      display: 'grid',
      placeItems: 'center',
      padding: '32px',
      background: 'var(--bg-primary)',
      color: 'var(--text-primary)',
    }}>
      <div style={{ textAlign: 'center', maxWidth: 420 }}>
        <p style={{ margin: 0, fontSize: 12, letterSpacing: '0.16em', textTransform: 'uppercase', opacity: 0.7 }}>
          404
        </p>
        <h1 style={{ margin: '12px 0 8px', fontSize: '2rem' }}>Page not found</h1>
        <p style={{ margin: 0, lineHeight: 1.6, opacity: 0.8 }}>
          The requested route does not exist in this workbench.
        </p>
        <Link
          to="/"
          style={{
            display: 'inline-block',
            marginTop: 20,
            padding: '10px 16px',
            borderRadius: 999,
            background: 'var(--accent)',
            color: '#fff',
            textDecoration: 'none',
            fontWeight: 600,
          }}
        >
          Return to workbench
        </Link>
      </div>
    </main>
  )
}