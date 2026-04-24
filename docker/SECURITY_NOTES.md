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

All handlers below remain in the source file but are **not registered**
when `CUA_ENABLE_LEGACY_ACTIONS` is unset or `0`. An unknown-action
request produces:

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{"success": false, "error": "Unknown or disabled action: '<name>'"}
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
