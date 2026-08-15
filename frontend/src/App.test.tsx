import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import App from './App'

vi.stubGlobal('fetch', vi.fn((input: RequestInfo) => {
  const url = String(input)
  const body = url.includes('/desktop') ? { viewerUrl: '/vnc/vnc.html?autoconnect=1' } : { data: [] }
  return Promise.resolve(new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } }))
}))

it('provides all five operational workspaces', async () => {
  render(<MemoryRouter><App /></MemoryRouter>)
  for (const label of ['Live session', 'Audit trail', 'Workflow library', 'Providers', 'Analytics']) {
    expect(screen.getByRole('link', { name: new RegExp(label, 'i') })).toBeInTheDocument()
  }
  expect(await screen.findByTitle('Sandbox desktop')).toHaveAttribute('src', '/vnc/vnc.html?autoconnect=1&path=vnc%2Fwebsockify')
  expect(screen.queryByText('No session on the wire')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('link', { name: /providers/i }))
  expect(await screen.findByRole('heading', { name: /provider access/i })).toBeInTheDocument()
  expect(screen.getByLabelText(/API key/i)).toHaveAttribute('type', 'password')
})

it('confirms before requesting a full application shutdown', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  render(<MemoryRouter><App /></MemoryRouter>)
  const button = screen.getByRole('button', { name: /stop app and background processes/i })

  await userEvent.click(button)
  expect(fetch).not.toHaveBeenCalledWith('/api/v2/system/shutdown', expect.anything())

  confirm.mockReturnValue(true)
  await userEvent.click(button)
  expect(fetch).toHaveBeenCalledWith('/api/v2/system/shutdown', expect.objectContaining({ method: 'POST' }))
})
