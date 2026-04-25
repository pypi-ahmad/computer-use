/**
 * Workbench-scoped constants.
 *
 * Extracted verbatim from ``Workbench.jsx`` (pre-PR). No values
 * changed — the split is purely to allow the new panel components
 * to import what they render without pulling in the whole page
 * module and its side-effect CSS import.
 */

import {
  MousePointer2, Keyboard, Type as TypeIcon, ScrollText, Globe, ArrowLeft,
  ArrowRight, Timer, Clipboard, Copy, RefreshCw, Plus, X as XIcon,
  Shuffle, Search, Monitor, Rocket, Camera, CheckCircle2, AlertCircle,
  Zap,
} from 'lucide-react'

export const PROVIDERS = [
  { value: 'google', label: 'Google Gemini', envVar: 'GOOGLE_API_KEY', placeholder: 'Paste your Google API key' },
  { value: 'anthropic', label: 'Anthropic Claude', envVar: 'ANTHROPIC_API_KEY', placeholder: 'Paste your Anthropic API key' },
  { value: 'openai', label: 'OpenAI GPT-5.4', envVar: 'OPENAI_API_KEY', placeholder: 'Paste your OpenAI API key' },
]

export const ICON_SIZE = 14

export const ACTION_ICON_MAP = {
  click: MousePointer2, double_click: MousePointer2, right_click: MousePointer2, hover: MousePointer2,
  type: Keyboard, fill: TypeIcon, key: Keyboard, hotkey: Keyboard,
  paste: Clipboard, copy: Copy,
  open_url: Globe, reload: RefreshCw, go_back: ArrowLeft, go_forward: ArrowRight,
  new_tab: Plus, close_tab: XIcon, switch_tab: Shuffle,
  scroll: ScrollText, scroll_to: ScrollText,
  get_text: Search, find_element: Search, evaluate_js: Monitor,
  focus_window: Monitor, open_app: Rocket,
  wait: Timer, wait_for: Timer, screenshot_region: Camera,
  done: CheckCircle2, error: AlertCircle,
}

export const ACTION_LABEL_MAP = {
  click: 'Clicked', double_click: 'Double-clicked', right_click: 'Right-clicked', hover: 'Hovered',
  type: 'Typed text', fill: 'Filled field', key: 'Pressed key', hotkey: 'Pressed keys',
  paste: 'Pasted', copy: 'Copied',
  open_url: 'Opened URL', reload: 'Reloaded page', go_back: 'Went back', go_forward: 'Went forward',
  new_tab: 'Opened new tab', close_tab: 'Closed tab', switch_tab: 'Switched tab',
  scroll: 'Scrolled', scroll_to: 'Scrolled to',
  get_text: 'Read text', find_element: 'Found element', evaluate_js: 'Ran script',
  focus_window: 'Switched window', open_app: 'Opened app',
  wait: 'Waited', wait_for: 'Waited for', screenshot_region: 'Captured region',
  done: 'Finished', error: 'Error',
}

export const MODEL_HINTS = {
  'gemini-3-flash-preview': { hint: 'Fast and affordable — good for simple tasks', tier: 'Budget' },
  'gemini-3.1-pro-preview': { hint: 'Stronger reasoning — use when Flash is not enough', tier: 'Mid-range' },
  'claude-sonnet-4-6': { hint: 'Balanced speed and capability — recommended for most tasks', tier: 'Mid-range', recommended: true },
  'claude-opus-4-7': { hint: 'Most capable — best for complex multi-step tasks', tier: 'Premium' },
  'gpt-5.4': { hint: 'OpenAI\'s built-in computer use model', tier: 'Mid-range' },
}

export const TASK_EXAMPLES = [
  'Open Chrome and search for "weather in New York"',
  'Open LibreOffice Writer and type a short letter',
  'Open the file manager and create a folder called "Projects"',
  'Open the terminal and check the current date',
  'Open Chrome and navigate to wikipedia.org',
]

export const SETTINGS_KEY = 'cua_settings_v1'
export const MAX_TASK_LENGTH = 10000

/** Return the lucide icon component for an action name, or ``Zap`` as fallback. */
export function getActionIcon(action) {
  const Icon = ACTION_ICON_MAP[action] || Zap
  return Icon
}
