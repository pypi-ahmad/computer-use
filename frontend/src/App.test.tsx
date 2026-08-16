import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import App from './App'

vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const body = url.includes('/desktop') ? { viewerUrl: '/vnc/vnc.html?autoconnect=1' } : { data: [] }
  return Promise.resolve(new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } }))
}))

it('provides all six operational workspaces', async () => {
  render(<MemoryRouter><App /></MemoryRouter>)
  for (const label of ['Live session', 'Audit trail', 'Session cost', 'Workflow library', 'Providers', 'Analytics']) {
    expect(screen.getByRole('link', { name: new RegExp(label, 'i') })).toBeInTheDocument()
  }
  const mission = await screen.findByText('Mission control')
  expect(mission.closest('.sidebar')).toBeTruthy()
  expect(screen.getByText(/Viewport \/ 1440/).closest('.sidebar')).toBeNull()
  const desktop = await screen.findByTitle('Sandbox desktop')
  expect(desktop).toHaveAttribute('src', '/vnc/vnc.html?autoconnect=1&path=vnc%2Fwebsockify')
  expect(desktop.closest('.live-grid')).toBeTruthy()
  expect(mission.closest('.live-grid')).toBeNull()
  expect(screen.getByRole('region', { name: /session log/i })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /copy logs/i })).toBeDisabled()
  expect(screen.getByText(/no log lines yet/i)).toBeInTheDocument()
  expect(screen.queryByText('No session on the wire')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('link', { name: /providers/i }))
  expect(await screen.findByRole('heading', { name: /provider access/i })).toBeInTheDocument()
  expect(screen.getByLabelText(/API key/i)).toHaveAttribute('type', 'password')
})

it('shows current-session cost and list rates on the Session cost tab', async () => {
  render(<MemoryRouter initialEntries={['/cost']}><App /></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: /session cost/i })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: /model/i })).toBeInTheDocument()
  expect(screen.getByText('Sonnet 5')).toBeInTheDocument()
  expect(screen.getByText('Gemini Flash 3.7')).toBeInTheDocument()
  expect(screen.getByText('Gemini 3.5 Flash Lite')).toBeInTheDocument()
  expect(screen.getByText('GPT 5.6 Luna')).toBeInTheDocument()
  expect(screen.getByText('GPT 5.6 Terra')).toBeInTheDocument()
  expect(screen.getByText(/no session yet/i)).toBeInTheDocument()
})

it('shows the audit journal, last 8 events, and ZIP export', async () => {
  render(<MemoryRouter initialEntries={['/audit']}><App /></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: /session audit trail/i })).toBeInTheDocument()
  expect(screen.getByText(/confirmed action journal/i)).toBeInTheDocument()
  expect(screen.getByText(/last 8 events/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /export zip/i })).toBeDisabled()
})

it('toggles provider web search via MCP fetch without submitting the form', async () => {
  render(<MemoryRouter><App /></MemoryRouter>)
  const toggle = await screen.findByRole('switch', { name: /provider web search/i })
  expect(toggle).toHaveAttribute('aria-checked', 'false')
  await userEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-checked', 'true')
  expect(toggle).toHaveTextContent(/on/i)
  await userEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-checked', 'false')
  expect(fetch).not.toHaveBeenCalledWith('/api/v2/sessions', expect.anything())
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
