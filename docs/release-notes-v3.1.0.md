# Computer Use Workbench v3.1.0

Released 2026-08-16.

## Highlights

- Gemini 3.7 Flash is the default Google Computer Use model. Gemini 3.5
  Flash-Lite and GPT-5.6 Terra are selectable on the existing routes.
- The Live tab shows the sandbox desktop as soon as the container is
  ready. You do not start a run to see the screen.
- `run.cmd` is the one-file Windows setup-if-needed launcher.
- noVNC uses `/vnc/websockify` through the workbench proxy, so the
  viewport no longer fails with "Failed to connect to server".

Review `CHANGELOG.md` and `USAGE.md` before upgrading an existing
deployment.
