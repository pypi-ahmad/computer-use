# CUA — Product Improvement Roadmap

*Planning document — no code changes. Based on full review of the current codebase as of April 2026.*

---

## 1. Objective

### What we are trying to improve

Make CUA feel like a reliable, understandable product that a non-technical user can open, understand immediately, and operate confidently — rather than a developer prototype that requires prior knowledge of Docker, WebSockets, and API key mechanics.

### Who the target users are

- **Primary:** AI/ML engineers and researchers already familiar with LLM tooling — but who should not need to fight the UI.
- **Stretch target:** Technical product managers, QA leads, and demo audiences who need to observe, evaluate, or showcase autonomous agents without touching the terminal.

### What "business-friendly" and "non-technical-user-friendly" means for this app

- **Business-friendly:** The app can be demoed to a stakeholder in under 2 minutes without the presenter apologizing for anything. Sessions produce shareable results. The interface communicates trust and predictability.
- **Non-technical-user-friendly:** A user who has never seen Docker, doesn't know what a WebSocket is, and has never used an LLM API key can still: (a) understand what the app does, (b) get a container running, (c) start their first agent task, and (d) understand what happened when it finishes — all without reading external documentation.

---

## 2. Current-State Summary

### The app today

CUA is a functional three-tier system (React frontend → FastAPI backend → Docker sandbox) that successfully lets users assign natural-language tasks to AI models that control a virtual Linux desktop. The core loop (perceive → think → act) works across three major providers (Gemini, Claude, GPT-5.4). The backend is well-validated with 118 passing tests, input validation, rate limiting, and Docker sandboxing.

### Biggest UX / business-readiness gaps

| Gap | Severity |
|-----|----------|
| **No safety confirmation UI** — the backend supports `require_confirmation` prompts, but the frontend has no dialog/modal to surface them. The agent hangs silently. | Critical |
| **No onboarding or first-run guidance** — a new user sees "Container Down" with no context. | High |
| **Two pages (`/` and `/workbench`) with duplicated, inconsistent functionality** — unclear which to use. | High |
| **Jargon-heavy copy** — "Container", "WS Connected", "CU Protocol", "Engine", ".env" shown to all users. | High |
| **No session completion summary** — when the agent finishes, there's no "here's what happened" feedback. | High |
| **No settings persistence** — everything resets on page refresh. | Medium |
| **No loading indicators** — starting the container, starting the agent, and loading models all lack spinners or progress. | Medium |
| **Accessibility failures** — 10-11px text, insufficient color contrast, timeline not keyboard-accessible. | Medium |
| **Vestigial UI elements** — disabled "Browser mode" toggle with historical tooltip, single-option dropdowns for Engine and Execution Target. | Low |

### Biggest first-time-user problems

1. No explanation of what CUA is or does anywhere in the UI.
2. "Container Down" red status is alarming and not actionable — there is no clear "Start" button associated with it.
3. The relationship between the main page and the Workbench is unexplained; users don't know which page to use.
4. API key entry gives no feedback on whether the key is valid before starting a session.
5. If the agent hits a safety confirmation checkpoint, the user has no way to respond.

---

## 3. Guiding Principles

1. **Do not break working features.** The core engine loop, multi-provider support, Docker sandbox, and test suite must remain fully intact.
2. **Simplify before adding.** Remove unnecessary UI elements (single-option dropdowns, vestigial toggles) before introducing new features.
3. **Reduce jargon.** Replace infrastructure terms with outcome-oriented language ("Environment" not "Container", "Connected" not "WS Connected").
4. **Use better defaults and hide what's fixed.** If there's only one engine, one target, one mode — don't show a dropdown.
5. **Progressive disclosure.** Show Provider + Model + Task upfront; tuck Engine, Execution Target, Reasoning Effort, Max Steps behind an "Advanced" section.
6. **Make user intent clear.** Every button should state what it does ("Start Environment", not "Start Container First"). Every status should suggest what to do next.
7. **Improve trust and clarity first.** Loading states, error boundaries, completion summaries, and the safety confirmation UI are more important than new features.
8. **Consistency over novelty.** One page, one design system, one labeling convention.

---

## 4. Prioritized Roadmap

---

### Phase 1 — Must-Do Now

**Goal:** Fix critical safety/trust gaps and eliminate the most confusing elements.

**Problems being solved:**
- Users cannot respond to safety confirmation prompts (agent hangs).
- No loading/feedback states makes the app feel broken during waits.
- Jargon and dead UI elements confuse first-time users.
- No error boundary means a React crash shows a blank page.

**Specific changes recommended:**

| # | Change | Detail |
|---|--------|--------|
| 1.1 | **Build the safety confirmation UI** | Add a modal/dialog that appears when the backend broadcasts a `safety_confirmation` WebSocket event. Show the action being requested, Approve/Deny buttons, and a visible countdown (60s). This is the most critical missing piece — without it, the confirmation feature is unusable. |
| 1.2 | **Add a React error boundary** | Wrap the app in an error boundary that shows a "Something went wrong — reload the page" message instead of a blank screen. |
| 1.3 | **Add loading indicators** | Show a spinner or disabled + loading state for: model list fetching, container start, agent start. Currently all three complete silently. |
| 1.4 | **Replace jargon in status pills** | `"Container Up"` → `"Environment Ready"`, `"Container Down"` → `"Environment Offline"`, `"WS Connected"` → `"Connected"`, `"WS Disconnected"` → `"Reconnecting…"`, `"Agent Service Ready"` → remove or fold into environment status. |
| 1.5 | **Remove single-option dropdowns** | Hide the Engine dropdown (only `computer_use`) and Execution Target dropdown (only `docker`). They add complexity with no user choice. Send the only valid values in the API call automatically. |
| 1.6 | **Remove the vestigial Browser Mode toggle** | The disabled "Desktop Only" button with the "Browser mode was removed" tooltip is confusing. Remove it entirely. |
| 1.7 | **Add a completion summary** | When the agent finishes, display a brief summary: total steps taken, final status (success/error/stopped), duration, and the last action performed. Replace the silent status-pill change with an explicit "Task Complete" or "Task Failed" banner. |
| 1.8 | **Add a 404 route** | Catch unmatched routes and show a "Page not found — Go to Dashboard" message. |

**Expected user impact:** The app stops feeling broken during waits, stops hanging on safety prompts, and stops showing meaningless developer jargon. First-time users can understand status at a glance.

**Business impact:** The app becomes demo-able without caveats. Safety gates actually work end-to-end.

**Risk level:** Low-Medium. Changes are additive (error boundary, modals, loading states) or subtractive (removing unused dropdowns). No backend changes needed except potentially adding a WebSocket event type for safety prompts if not already broadcast.

**Dependencies:** The safety confirmation modal depends on understanding the exact WebSocket event shape for `safety_confirmation`. Verify the backend already broadcasts this.

---

### Phase 2 — Should-Do Next

**Goal:** Unify the experience, improve onboarding, and make the app self-explanatory.

**Problems being solved:**
- Two pages with duplicated functionality cause confusion.
- No onboarding means users don't know what the app does or how to start.
- Settings don't persist; API keys and preferences are lost on refresh.
- Task input gives no guidance; no templates or examples.
- Accessibility issues exclude some users.

**Specific changes recommended:**

| # | Change | Detail |
|---|--------|--------|
| 2.1 | **Consolidate into a single page** | The main page (`/`) and Workbench (`/workbench`) duplicate provider selection, API key input, model selection, task input, start/stop, and screenshot display. Merge into one page that combines the ControlPanel's simplicity with the Workbench's timeline and log features. Keep `/workbench` as a redirect to `/` for any existing links. |
| 2.2 | **Add a first-run welcome state** | When no session has been run and the container is offline, show a centered welcome card: "CUA lets you give tasks to an AI agent that controls a virtual computer. Step 1: Start the environment. Step 2: Choose a model. Step 3: Describe a task." Replace the SVG monitor + "No screen capture" empty state. |
| 2.3 | **Add progressive disclosure for advanced settings** | Show only Provider, Model, API Key, and Task by default. Put Max Steps, Reasoning Effort (OpenAI), and any future settings behind a collapsible "Advanced Settings" section. |
| 2.4 | **Persist settings in localStorage** | Save the selected provider, model, key source preference, max steps, and reasoning effort to `localStorage`. Do NOT persist the API key itself (security). Restore on page load. |
| 2.5 | **Add task examples / templates** | Below the task textarea, show 3–5 clickable example tasks: "Open Chrome and search for…", "Open the file manager and create a folder…", "Open LibreOffice Writer and type a letter…". Clicking one fills the textarea. |
| 2.6 | **Fix accessibility: font sizes, contrast, keyboard** | Increase minimum font size to 12px (currently 10-11px in many places). Fix `--text-secondary` contrast on `--bg-secondary` (currently ~3.8:1, needs ≥4.5:1). Add `role="button"`, `tabIndex`, and `onKeyDown` to clickable timeline items. Add `aria-label` to icon-only buttons. |
| 2.7 | **Add a "Ctrl+Enter to start" hint** | Show a subtle hint near the Start button: `"Ctrl+Enter"`. The shortcut already works but is undiscoverable. |
| 2.8 | **Improve error messages to be actionable** | `"Failed to start Docker container"` → `"Could not start the environment. Make sure Docker Desktop is running."`. `"API key is required. Provide it in the UI, .env file, or system environment variable."` → `"Please enter your API key above to continue."` |
| 2.9 | **Add a character count to the task textarea** | Show a `X / 10,000` counter so users know the limit exists and how close they are. |
| 2.10 | **Add basic responsive layout** | At ≤1000px the Workbench right panel vanishes entirely. Replace `display: none` with a tabbed layout or accordion so timeline/logs remain accessible. Fix the main dashboard's fixed 380px sidebar to be responsive. |

**Expected user impact:** The app becomes self-explanatory for a first-time user. Settings survive page refreshes. The two-page confusion is eliminated. Accessibility improves for users with vision or motor constraints.

**Business impact:** Demo-ready in a corporate setting. Non-technical stakeholders can use the app independently. The app meets basic WCAG 2.1 AA compliance.

**Risk level:** Medium. Consolidating two pages is the highest-risk item — it requires careful merging of two sets of state management. All other items are additive or CSS-only.

**Dependencies:**
- 2.1 (page consolidation) should be done before other Phase 2 UI work to avoid building on duplicate pages.
- 2.6 (accessibility) is independent and can be done in parallel.

---

### Phase 3 — Nice-to-Have Later

**Goal:** Add polish, enable sharing, and support business workflows.

**Problems being solved:**
- No session history or persistence — past work is lost.
- No shareable artifacts — can't show stakeholders what happened.
- Emoji icons look unprofessional.
- No cost awareness.
- No API key validation before starting.

**Specific changes recommended:**

| # | Change | Detail |
|---|--------|--------|
| 3.1 | **Session history panel** | Persist completed sessions (task, steps, timestamps, final status) in localStorage or IndexedDB. Show a session list in a sidebar or dropdown. Allow reviewing past session timelines. |
| 3.2 | **Session export** | Allow downloading a session as a structured JSON or HTML report (task, steps with screenshots, final result). More useful than the current raw `.txt` log download. |
| 3.3 | **Replace emoji icons with an icon library** | Swap 🖱️ ⌨️ 📜 🧠 📄 💻 ✏️ etc. with SVG icons from a library like Lucide or Heroicons. Emoji rendering is inconsistent across OS and looks unprofessional in business contexts. |
| 3.4 | **Add favicon and meta tags** | Add a proper favicon, `<meta name="description">`, and Open Graph tags to `index.html`. Currently there is no favicon and the title is just "CUA". |
| 3.5 | **API key pre-validation** | Before starting a session, make a lightweight validation call (or expose a `/api/keys/validate` endpoint) to confirm the key works. Show a green checkmark or red X next to the key field. |
| 3.6 | **Cost estimation indicator** | Show approximate cost per step for the selected model (even a rough "~$0.01–0.05 per step" range). WARN when max_steps × cost approaches a notable amount. |
| 3.7 | **Toast notifications** | Add a lightweight toast system for transient feedback: "Environment started", "Agent stopped", "Session complete — 12 steps". Replace the current silent state transitions. |
| 3.8 | **Swagger UI link** | FastAPI auto-generates `/docs`. Add a small "API Docs" link in the header for developers. Currently inaccessible from the UI. |
| 3.9 | **Configurable CORS origins** | CORS is hardcoded to `localhost:3000` and `localhost:5173`. Add an env var (`CORS_ORIGINS`) so the app can be deployed to other origins without code changes. |
| 3.10 | **Light theme option** | Add a light/dark theme toggle. The current dark-only theme may not suit all corporate presentation contexts. |

**Expected user impact:** The app feels like a product, not a prototype. Past work is recoverable. Demos produce shareable artifacts.

**Business impact:** The app supports iterative evaluation workflows (history), stakeholder communication (export), and deployment flexibility (CORS).

**Risk level:** Low for most items. Session persistence (3.1) has medium risk due to storage management and potential stale data. Cost estimation (3.6) requires maintaining accurate pricing data.

**Dependencies:**
- 3.2 (export) depends on 3.1 (history) or at minimum on the completion summary from Phase 1.
- 3.3 (icons) adds a dependency on an icon library.

---

## 5. Workstream Breakdown

---

### WS-1: UX / Navigation

**Why it matters:** Users currently face two pages with overlapping functionality, an unexplained Workbench link, and no 404 route. The navigation structure actively creates confusion.

**Top improvements:**
1. Consolidate `/` and `/workbench` into a single page (2.1)
2. Add a 404 route (1.8)
3. Add a first-run welcome state (2.2)

**Priority:** High  
**Risk:** Medium (page consolidation is the riskiest single change in the roadmap)

---

### WS-2: Forms / Validation

**Why it matters:** Form fields use inconsistent labels between the two pages, include non-functional dropdowns, lack character counts, and provide no pre-flight validation of API keys.

**Top improvements:**
1. Remove single-option dropdowns — Engine, Execution Target (1.5)
2. Remove vestigial Browser Mode toggle (1.6)
3. Add progressive disclosure for advanced settings (2.3)
4. Add task character counter (2.9)
5. Pre-validate API keys before session start (3.5)

**Priority:** High (1.5, 1.6), Medium (2.3, 2.9), Low (3.5)  
**Risk:** Low — all are additive or subtractive changes to the form UI

---

### WS-3: Copy / Labels / Microcopy

**Why it matters:** Jargon-heavy status indicators ("Container", "WS", "CU Protocol", ".env") make the app feel like a developer tool, not a product. Error messages reference infrastructure concepts instead of guiding the user.

**Top improvements:**
1. Replace jargon in status pills (1.4)
2. Rewrite error messages to be actionable (2.8)
3. Add `Ctrl+Enter` shortcut hint (2.7)
4. Add task example templates (2.5)

**Priority:** High (1.4), Medium (2.7, 2.8, 2.5)  
**Risk:** Very low — text-only changes

---

### WS-4: Onboarding / Guidance

**Why it matters:** There is zero onboarding. A first-time user sees an empty screen with a red "Container Down" label and no idea what to do next.

**Top improvements:**
1. Add a first-run welcome state with step-by-step guidance (2.2)
2. Add clickable task examples (2.5)
3. Improve the empty screen state to be instructional, not just a placeholder SVG (2.2)

**Priority:** Medium (not blocking usage for technical users, but blocking adoption by non-technical users)  
**Risk:** Low — additive UI

---

### WS-5: Settings / Configuration Simplification

**Why it matters:** Settings don't persist, advanced options clutter the main form, and env-var-only configuration is invisible to UI users.

**Top improvements:**
1. Persist settings in localStorage (2.4)
2. Progressive disclosure for advanced settings (2.3)
3. Hide fixed-value dropdowns (1.5)
4. Configurable CORS origins via env var (3.9)

**Priority:** Medium  
**Risk:** Low-Medium (localStorage persistence needs careful handling of stale data)

---

### WS-6: Trust / Professionalism / Polish

**Why it matters:** Missing loading states, silent errors, no completion summary, and emoji icons undermine confidence. The safety confirmation feature — a key trust mechanism — is non-functional in the frontend.

**Top improvements:**
1. Safety confirmation modal (1.1) — **critical**
2. Loading indicators (1.3)
3. Error boundary (1.2)
4. Completion summary (1.7)
5. Toast notifications (3.7)
6. Replace emoji with SVG icons (3.3)
7. Add favicon and meta tags (3.4)

**Priority:** Critical (1.1), High (1.2, 1.3, 1.7), Low (3.3, 3.4, 3.7)  
**Risk:** Low for most. Safety confirmation modal is medium risk — must match the backend's exact event/response protocol.

---

### WS-7: Accessibility / Readability

**Why it matters:** Multiple text elements are 10-11px, color contrast fails WCAG AA on secondary text, the timeline is not keyboard-navigable, and the responsive layout breaks at ≤1000px by hiding entire panels.

**Top improvements:**
1. Increase base font size to ≥12px (2.6)
2. Fix color contrast ratios for secondary text and status pills (2.6)
3. Add keyboard accessibility to timeline items (2.6)
4. Add `aria-label` to icon-only buttons (2.6)
5. Responsive layout for narrow viewports (2.10)

**Priority:** Medium  
**Risk:** Low — CSS and attribute-level changes

---

## 6. Quick Wins

Highest impact, lowest risk. Can each be done in isolation in a few hours or less.

| # | Quick Win | Impact | Risk | Phase |
|---|-----------|--------|------|-------|
| 1 | Replace jargon in status pills (1.4) | High — immediately less confusing | Very low — string changes only | 1 |
| 2 | Remove single-option dropdowns (1.5) | Medium — less visual clutter | Very low — delete JSX, send hardcoded values | 1 |
| 3 | Remove vestigial Browser Mode toggle (1.6) | Low-Medium — cleaner UI | Very low — delete JSX | 1 |
| 4 | Add a 404 route (1.8) | Low — prevents blank-page surprise | Very low — one route in React Router | 1 |
| 5 | Add a React error boundary (1.2) | High — prevents blank-screen crashes | Very low — wrap App component | 1 |
| 6 | Add `Ctrl+Enter` shortcut hint near Start button (2.7) | Low — makes existing feature discoverable | Very low — add a `<span>` | 2 |
| 7 | Add favicon and meta tags (3.4) | Low — professionalism | Very low — edit `index.html` | 3 |
| 8 | Add task character counter (2.9) | Low — prevents surprise rejection | Very low — computed value + `<span>` | 2 |
| 9 | Add `aria-label` to icon-only buttons (2.6) | Medium for a11y — screen reader support | Very low — add attributes | 2 |
| 10 | Improve actionable error messages (2.8) | Medium — reduces confusion | Very low — string changes | 2 |

---

## 7. Risky or Sensitive Areas

### 7.1 Safety Confirmation Modal (1.1)

**Why it's risky:** This bridges the WebSocket event layer and a critical security feature. If the modal fails to appear, the agent hangs. If it auto-approves, it defeats the safety mechanism. The 60-second timeout default-deny behavior must be reflected accurately in the UI.

**How to approach safely:**
- First, verify the exact WebSocket event shape broadcast by the backend. Trace the code path from `backend/server.py` safety-confirm endpoint through to the WebSocket broadcast.
- Build the modal as a standalone component with its own tests.
- Test: event arrives → modal appears → countdown visible → Approve sends correct payload → Deny sends correct payload → Timeout triggers deny.
- Never auto-approve. Never hide the modal behind another layer.

### 7.2 Page Consolidation (2.1)

**Why it's risky:** The main page (`/`) and Workbench (`/workbench`) have independently evolved state management, API call patterns, and provider/model handling. Merging them could introduce regressions in either flow. ControlPanel uses inline styles; Workbench uses CSS classes. Both have their own `useEffect` hooks for model fetching and WebSocket handling.

**How to approach safely:**
- Start by extracting shared logic (provider state, model fetching, API key management, agent start/stop) into a custom hook or context provider. Test the shared logic independently.
- Pick one page's layout as the base (Workbench is more complete). Migrate the other page's unique features into it.
- Keep `/` alive as a redirect to the consolidated page for a release cycle.
- Verify both existing flows work identically before removing the old page.

### 7.3 localStorage Persistence (2.4)

**Why it's risky:** Stale persisted values (e.g., a model that was removed from `allowed_models.json`, or a provider whose key format changed) could cause confusing errors on load. Users may not realize old settings are being restored.

**How to approach safely:**
- Version the localStorage schema. If the stored version doesn't match, clear and use defaults.
- On load, validate stored values against the current model list from the API.
- Never persist API keys.
- Show a brief visual indicator when settings are restored from storage ("Settings restored from your last session").

### 7.4 Responsive Layout Changes (2.10)

**Why it's risky:** The current `display: none` for the right panel at ≤1000px was an intentional choice. Replacing it with a tabbed layout changes the information architecture on smaller screens and could introduce scrolling, overflow, or z-index issues.

**How to approach safely:**
- Prototype the tabbed layout in isolation.
- Test on actual narrow viewports (phone, tablet), not just resized desktop windows.
- Ensure no content is lost — timeline and logs must remain accessible at all breakpoints.

---

## 8. Suggested Rollout Order

The safest sequence, grouped into deployable batches:

### Batch A — Cleanup & Safety Net (Phase 1, low risk)
1. Add React error boundary (1.2)
2. Remove single-option dropdowns (1.5)
3. Remove vestigial Browser Mode toggle (1.6)
4. Replace jargon in status pills (1.4)
5. Add 404 route (1.8)

*Deploy and verify nothing broke.*

### Batch B — Core Trust Features (Phase 1, medium risk)
6. Add loading indicators for container start, agent start, model fetch (1.3)
7. Build and ship the safety confirmation modal (1.1)
8. Add completion summary banner (1.7)

*Deploy. Verify safety confirmation flow end-to-end. Verify completion summary for success, failure, and manual-stop cases.*

### Batch C — Onboarding & Copy (Phase 2, low risk)
9. Add first-run welcome state (2.2)
10. Add task example templates (2.5)
11. Rewrite error messages (2.8)
12. Add Ctrl+Enter hint (2.7)
13. Add task character counter (2.9)

*Deploy. Test first-time-user flow.*

### Batch D — Accessibility (Phase 2, low risk)
14. Fix font sizes (2.6)
15. Fix color contrast (2.6)
16. Add keyboard accessibility to timeline (2.6)
17. Add aria-labels (2.6)

*Deploy. Run automated a11y audit.*

### Batch E — Architecture Consolidation (Phase 2, medium risk)
18. Extract shared logic into custom hooks (prerequisite for 2.1)
19. Consolidate into single page (2.1)
20. Add progressive disclosure for advanced settings (2.3)
21. Add localStorage persistence (2.4)
22. Add responsive layout (2.10)

*Deploy. Full regression test of all user flows.*

### Batch F — Polish (Phase 3, low risk)
23. Replace emoji with SVG icons (3.3)
24. Add favicon and meta tags (3.4)
25. Add toast notifications (3.7)
26. Add Swagger UI link (3.8)
27. Configurable CORS origins (3.9)

### Batch G — Business Features (Phase 3, medium risk)
28. API key pre-validation (3.5)
29. Session history in localStorage/IndexedDB (3.1)
30. Session export as JSON/HTML (3.2)
31. Cost estimation indicator (3.6)
32. Light theme option (3.10)

---

## 9. Success Criteria

The app is considered "business-friendly and non-technical-user-friendly" when:

| Criteria | Measurement |
|----------|-------------|
| **First-task completion without external help** | A new user who has only an API key can start the environment, run a task, and understand the result within 5 minutes with no documentation or guidance beyond what the UI provides. |
| **Safety gates work end-to-end** | When the AI model requests confirmation, a visible modal appears, the user can approve or deny, and timeout correctly defaults to deny. Verified by manual test. |
| **No jargon on the default view** | A non-technical user can read every status label, button, and message on the page without encountering Docker, WebSocket, CU Protocol, .env, or engine terminology. |
| **Settings survive refresh** | Provider, model, max steps, and reasoning effort persist across browser sessions. API keys do not persist (by design). |
| **Completion is communicated** | Every session ends with a visible summary (steps taken, outcome, duration) — not a silent status-pill change. |
| **No blank-page crashes** | The error boundary catches React crashes and shows a recovery message. Tested by deliberately triggering an error. |
| **Loading states are visible** | Every async operation (container start, agent start, model fetch) shows a spinner or progress indicator. No "frozen button" moments. |
| **Accessibility baseline** | Minimum 12px font size, ≥4.5:1 contrast ratio on all text, all interactive elements keyboard-accessible. Passes automated WCAG 2.1 AA audit with zero critical violations. |
| **Single coherent page** | Users do not encounter two different UIs for the same task. One page, one set of controls, one mental model. |
| **Demo-ready in 2 minutes** | A presenter can open the app, start the environment, run a task, and show the completion summary to a stakeholder in under 2 minutes with no pre-demo setup beyond having the app running. |

---

## 10. Final Recommendation

### Top 5 actions (in priority order)

1. **Build the safety confirmation modal** (1.1) — The most critical gap. A safety feature that exists in the backend but is invisible in the frontend is worse than no safety feature; it creates a hung session with no user recourse.

2. **Add loading states and error boundary** (1.2, 1.3) — The cheapest way to make the app feel reliable. Every async operation needs visible feedback.

3. **Replace jargon and remove dead UI elements** (1.4, 1.5, 1.6) — Instant clarity improvement with near-zero risk. Pure subtraction and rewording.

4. **Add first-run welcome state and task examples** (2.2, 2.5) — Transforms the empty screen from "what do I do?" to "here's how to start."

5. **Consolidate into a single page** (2.1) — Eliminates the most confusing structural problem. Needed before investing further in either page's UI.

### Safest first step

**Batch A** — Error boundary, remove dead dropdowns, remove vestigial toggle, replace jargon in status pills, add 404 route. All are low-risk, independent changes that immediately improve the experience. None alter backend behavior or data flow.

### Highest business impact step

**Safety confirmation modal (1.1)**. Without it, any demo or evaluation involving a model that triggers `require_confirmation` ends in a silent hang. With it, the app demonstrates responsible AI practices — a key selling point for business audiences evaluating agent tooling.
