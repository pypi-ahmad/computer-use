# Agent-service attack-surface notes

The in-container HTTP service (`docker/agent_service.py`) historically
exposed more actions than the engine ever invokes. As of PR
`<docker-action-trim>`, the default build serves exactly the action
surface the engine uses today. Everything else is gated behind
`CUA_ENABLE_LEGACY_ACTIONS=1` and, when disabled, returns **HTTP 404**
on `POST /action`.

## Live action set (always on)

Derived from `backend/engine/__init__.py::DesktopExecutor`. Any action
name outside this set is treated as unknown by default.

| Action           | Origin in engine                                       |
| ---------------- | ------------------------------------------------------ |
| `click`          | `_act_click_at`                                        |
| `double_click`   | `_act_double_click`, `_act_triple_click`               |
| `right_click`    | `_act_right_click`                                     |
| `middle_click`   | `_act_middle_click`                                    |
| `hover`          | `_act_hover_at`, `_act_move`                           |
| `type`           | `_act_type_text_at`, `_act_type_at_cursor`             |
| `hotkey`         | `_act_type_text_at` (select-all before typing)         |
| `key`            | `_act_type_text_at`, `_act_key_combination`, `_act_go_back`, `_act_go_forward`, `_act_type_at_cursor` (press_enter) |
| `keydown`        | `_act_hold_key`                                        |
| `keyup`          | `_act_hold_key`                                        |
| `scroll`         | `_act_scroll_document`, `_act_scroll_at`               |
| `left_mouse_down`| `_act_left_mouse_down`                                 |
| `left_mouse_up`  | `_act_left_mouse_up`                                   |
| `drag`           | `_act_drag_and_drop`                                   |
| `open_url`       | `_act_navigate`, `_act_open_web_browser`, `_act_search`|

`GET /screenshot?mode=desktop` and `GET /health` are unchanged — the
gate lives on `POST /action` only.

## Removed / flagged actions

All handlers below remain in the source file but are **rejected at the
`POST /action` gate before dispatch** when `CUA_ENABLE_LEGACY_ACTIONS`
is unset or `0`. An unknown-action request produces:

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{"success": false, "message": "Unknown or disabled action: '<name>'"}
```

| Action                          | Category        | Rationale for removal                                     | Replacement (if any)                                |
| ------------------------------- | --------------- | --------------------------------------------------------- | ---------------------------------------------------- |
| `focus_window`, `window_activate`, `close_window`, `search_window` | Window mgmt | Engine never emits; prompt-injected `close_window` would disrupt user session. | Use `click` on window chrome. |
| `window_minimize`, `window_maximize`, `window_move`, `window_resize` | Window mgmt | Same as above. Adds wmctrl attack surface. | None. |
| `focus_click`, `focus_mouse`, `mousemove`                          | Mouse helpers | Superseded by `hover` + `click`. | `hover` then `click`. |
| `open_app`, `open_terminal`                                        | App launch    | Arbitrary subprocess spawn; large surface. | None (use `open_url` for web). |
| `paste`, `copy`, `type_slow`                                       | Clipboard     | `xclip` + clipboard poisoning risk; `type_slow` just duplicates `type`. | `type`. |
| `fill`, `clear_input`, `select_option`                             | Form helpers  | Desktop approximations the engine never calls. | `click` + `hotkey("ctrl+a")` + `key("BackSpace")` + `type`. |
| `reload`, `go_back`, `go_forward` (HTTP action)                    | Nav shortcuts | Engine maps these to `key("F5")` / `key("alt+Left")` already. | `key` with the appropriate combo. |
| `new_tab`, `close_tab`, `switch_tab`, `scroll_to`                  | Browser UX    | Engine never emits. | `key("ctrl+t")`, `key("ctrl+w")`, `key("ctrl+1..9")`. |
| `get_text`, `find_element`, `evaluate_js`, `wait_for`              | DOM stubs     | All return "not supported" today — dead code path. | None; use pixel-based interaction. |
| `scroll_up`, `scroll_down`                                         | Scroll variants | Engine uses `scroll` with a direction. | `scroll` + `text=up|down`. |
| `screenshot`, `screenshot_full`, `screenshot_region`               | Vision        | Engine uses `GET /screenshot?mode=desktop`; POST variants are drift. Region screenshots were never used. | `GET /screenshot`. |
| `run_command`                                                      | Shell exec    | Largest single attack surface in the service. Allowlist + blocked-pattern defenses remain in source (enforced when flag is on) per PR 05. | None in default build. |
| `wait`                                                             | Synchronisation | Engine implements waits with `asyncio.sleep`, not an HTTP call. | In-process sleep in the engine. |

## Re-enabling legacy actions

```bash
docker run \
  -e CUA_ENABLE_LEGACY_ACTIONS=1 \
  -e AGENT_SERVICE_TOKEN="$TOKEN" \
  cua-environment
```

With the flag on, every handler listed above is reachable via
`POST /action`. The existing defenses (`run_command` allowlist,
`_BLOCKED_CMD_PATTERNS`, upload-path containment) still apply — the
flag gates *reachability*, not *enforcement*. When re-enabling
`run_command` in particular, review:

- [`_ALLOWED_COMMANDS`](agent_service.py) — strict executable allowlist
- [`_BLOCKED_CMD_PATTERNS`](agent_service.py) — case-insensitive pattern
  match over the full argv, enforced by `_blocked_cmd_match`
- [`_is_safe_upload_path`](agent_service.py) — symlink-aware path
  containment for upload helpers

## Detection signal

When the gate rejects an action, the service logs at INFO:

```
action rejected: action='open_app' resolved='open_app' legacy_enabled=False
```

Operators looking for unexpected client behaviour (old engine images,
prompt-injected action names) should grep container logs for
`action rejected:`.

## Viewport default (1440x900)

Union-of-best-practice across all CU providers:

- **Anthropic Opus 4.7** — native 1:1 coordinates. Opt into the
  higher native ceiling (2576px long-edge / ~3.75 MP) by setting
  `CUA_OPUS47_HIRES=1` in the backend env AND overriding
  `SCREEN_WIDTH` / `SCREEN_HEIGHT` up to 2560x1600 at `docker run`
  time. The backend enforces only the long-edge cap on this path
  (skips the 3.75 MP total-pixel cap) so hi-fidelity sessions keep
  1:1 coordinates.
- **Anthropic Sonnet 4.6 / Opus 4.6** — downscale internally; 1440x900
  is a no-op for them. Do **not** set `CUA_OPUS47_HIRES` for these
  models — it is gated by `_is_opus_47` and is a no-op outside Opus
  4.7.
- **OpenAI GPT-5.4** — the current guide's preferred viewport is
  1440x900 / 1600x900. The built-in `computer` tool infers display
  dimensions from the screenshot bytes, so no display_width /
  display_height kwargs are sent — the viewport we render IS the
  viewport the model sees.
- **Google Gemini 3 Flash** — docs recommend exactly 1440x900. The
  adapter normalises coordinates to the 0-999 grid before the
  `DesktopExecutor` denormalises them to real pixels.

Sandbox base is `ubuntu:24.04`. The Anthropic computer-use-demo
reference is `ubuntu:22.04`; we run a newer LTS here because the
XFCE4 / google-chrome-stable / noVNC stack is already tuned for
24.04 and downgrading would regress that work. The package set is
still the union of Anthropic's reference (`xvfb`, `xdotool`,
`scrot`, `imagemagick`, `mutter`, `x11vnc`, `firefox-esr`, `xterm`,
`tint2`, `xpdf`, `x11-apps`, `sudo`, `build-essential`,
`software-properties-common`, `netcat-openbsd`) and the existing
XFCE4 stack — nothing was removed.


### Sonnet 4.6 lineage note

Claude Sonnet 4.6 shares Opus 4.7's full-desktop sandbox requirements:
the same `computer_20251124` tool version, the same
`computer-use-2025-11-24` beta header, and the same Anthropic
computer-use-demo package baseline.  No Sonnet-4.6-specific
packages or viewport overrides exist in the image.

Sonnet 4.6 does **not** inherit Opus 4.7's 2576px / 1:1 coordinate
improvements — it keeps the 1568 px / 1.15 MP ceiling and downscales
anything larger internally.  `CUA_OPUS47_HIRES` is gated on
`_is_opus_47(model)` in `backend/engine/claude.py` and is
intentionally ignored for Sonnet 4.6 so the extra framebuffer tokens
cost nothing in coordinate accuracy.
