import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import App from './App'

vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: [] }), { headers: { 'content-type': 'application/json' } })))

it('provides all five operational workspaces', async () => {
  render(<MemoryRouter><App /></MemoryRouter>)
  for (const label of ['Live session', 'Audit trail', 'Workflow library', 'Providers', 'Analytics']) {
    expect(screen.getByRole('link', { name: new RegExp(label, 'i') })).toBeInTheDocument()
  }
  await userEvent.click(screen.getByRole('link', { name: /providers/i }))
  expect(await screen.findByRole('heading', { name: /provider access/i })).toBeInTheDocument()
  expect(screen.getByLabelText(/API key/i)).toHaveAttribute('type', 'password')
})
