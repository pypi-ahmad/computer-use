# Graph Report - D:\AI\Github\computer-use  (2026-08-12)

## Corpus Check
- 120 files · ~164,359 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2487 nodes · 4938 edges · 197 communities (133 shown, 64 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 707 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `aa5a80ce`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Session Models and Tests
- WebSocket Server API
- Provider Run Contracts
- Observability and Tracing
- Agent Loop and API
- Request Models and Tests
- Desktop Action Dispatch
- Claude and Engine Core
- Engine Validation Tests
- REST Origin and Auth
- React Workbench UI
- Engine Capability Schema
- Claude Client Tests
- Gemini Client and Tests
- Async Engine Test Helpers
- Claude Provider Client
- Action Execution Contract
- V2 API and Storage
- Engine Fake Clients
- V2 Execution Routing
- Sqlite Store
- Sanitize Openai Response Item
- Test Infra
- Test V2 Platform
- Agent Handler
- Compiler Options
- Config Components
- Desktop Executor
- Open Aicuclient
- Engine Openai
- Executor Components
- Test Open Aiscroll Clamp
- Docker Components
- Dev Components
- Open Url In Browser
- Test Claude Web Search
- Files Components
- File Store
- Test Config
- Agent Service
- Computer Use Workbench Readme
- Make Components
- Test Browser Security Posture
- Test Gap Coverage
- Engine Capabilities
- Gemini Changelog Watchdog
- Test Engine
- Get Components
- Engine Certifier
- Xdo Type Text At
- Test Agent Start Validation
- Get System Prompt
- Dev Dependencies
- Test Vnc Websockify Token
- Prune Claude Context
- Runtime Error
- Safety Components
- Test Client
- Test Agent Service
- Test Run Command Enforcement
- Fake Executor
- Test Claude Scale Factor
- Test Model Policy
- Get Claude Scale Factor
- Validate Name
- Engine Schema
- Models Components
- Test Blocked Cmd Match
- Value Error
- Add Stream
- Engine Report
- Credential Vault
- Frame Retention Store
- Test Action Gate
- Fake Executor
- Test Agent Handler Auth
- Test Env Clamping
- Resize Screenshot For Claude
- Configure Logging
- Validation Components
- Entrypoint Sh
- Test Container Readiness Gating
- Test Fixes Wave Apr2026
- From Env
- Dependencies Components
- Scripts Components
- Test Key Allowlist
- Test Fix Pass Remediation
- Test Upload Path Containment
- Prune Gemini Context
- Config Components
- Validate Outbound
- Test Public Bind Guardrail
- Test Start Container
- Vnc Http Proxy
- V2 Orchestrator
- Build Docs Site
- Test Web Socket Origin
- Agent Service
- Client Components
- Cua Computer Using Agent
- Capture Screenshot
- Mount Production Frontend
- Test Ready Agent Does
- Build Release
- Setup Sh
- Fake Message Stream
- Test Retry Policy Class
- Capture Thinking
- Test Engine Package Split
- Execute Components
- Format Components
- Allowed Models
- Computer Use Models V2
- Agent Loop
- Package Components
- Test Oci Labels
- Test Signal Clean Shutdown
- Test Dockerfile Viewport Default
- Text Block
- Is Allowed Key Token
- Get Many
- Parse Cors Origins
- Action Id Filter
- Xdo Type
- Offline Deterministic Evals
- Clear Active Registries
- Test Defenses Preserved
- Test Non Root Runtime
- Test Healthcheck
- Test In Container Alias
- Test Engine Action Set
- Test Scroll Mapping And
- Test Origin Gating
- Test Docker Lifecycle Lock
- Test Agent Service Subprocess
- Validated Http Url
- Container Hardening Posture
- Desktop Executor
- Study Handbook
- Test Concurrent Session Limit
- Test Cleanup Session Resilience
- Aclose Components
- Get Current Url
- V2 Components
- Code Of Conduct
- Dev Sh Script
- Credential Vault
- Cuaf Binary Frame Format
- Eslint Components
- Eslint Plugin React Hooks
- Eslint Plugin React Refresh
- Evals Components
- Container Readiness
- Cua Application Shell
- Jsdom Components
- Testing Library Jest Dom
- Types React
- Types React Dom
- Typescript Eslint
- Vite Components
- Vitest Coverage V8
- Test Action Id Uses
- Client Components
- Changelog Components
- Datasets Components
- Computer Use Agent
- Provider Native Web Search
- Safety Confirmation Handshake
- Dockerized Desktop Sandbox
- Sqlite Store
- Computer Use Workbench
- Anthropic Dependency
- Fast Api Dependency
- Google Gen Ai Dependency
- Httpx Dependency
- Open Ai Dependency
- Pillow Dependency
- Pydantic Dependency
- Pdf Dependency
- Python Docx Dependency
- Python Dotenv Dependency
- Python Multipart Dependency
- Uvicorn Dependency
- Web Sockets Dependency

## God Nodes (most connected - your core abstractions)
1. `OpenAICUClient` - 85 edges
2. `AgentLoop` - 69 edges
3. `ClaudeCUClient` - 63 edges
4. `DesktopExecutor` - 60 edges
5. `SessionStatus` - 58 edges
6. `GeminiCUClient` - 56 edges
7. `AgentSession` - 53 edges
8. `ActionType` - 42 edges
9. `CUActionResult` - 41 edges
10. `Config` - 37 edges

## Surprising Connections (you probably didn't know these)
- `HTML Study Handbook` --semantically_similar_to--> `PDF Study Handbook`  [INFERRED] [semantically similar]
  docs/zero-to-hero-study-handbook.html → docs/zero-to-hero-study-handbook.pdf
- `TestContainerReadinessGating` --uses--> `GeminiCUClient`  [INFERRED]
  tests/test_audit_fixes.py → backend/engine/gemini.py
- `TestKeyAllowlist` --uses--> `GeminiCUClient`  [INFERRED]
  tests/test_audit_fixes.py → backend/engine/gemini.py
- `TestOpenAIScrollClamp` --uses--> `GeminiCUClient`  [INFERRED]
  tests/test_audit_fixes.py → backend/engine/gemini.py
- `TestOriginGating` --uses--> `GeminiCUClient`  [INFERRED]
  tests/test_audit_fixes.py → backend/engine/gemini.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **v2 Release Assurance** — github_workflows_ci, github_workflows_release, docs_release_notes_v2_0_0, docs_deployment [INFERRED 0.85]
- **v2 Operational Safety Controls** — docs_computer_use_prompt_guide_approval_boundary, docker_security_notes_container_hardening, docs_rollback_v2_preserve_v2_state, technical_provider_neutral_checkpoint [INFERRED 0.75]
- **Shared Provider-to-Desktop Execution Pipeline** — docs_zero_to_hero_study_handbook_agent_loop, docs_zero_to_hero_study_handbook_computer_use_engine, docs_zero_to_hero_study_handbook_desktop_executor, docs_zero_to_hero_study_handbook_agent_service [EXTRACTED 1.00]
- **V2 Session Coordination** — docs_zero_to_hero_study_handbook_v2_orchestrator, docs_zero_to_hero_study_handbook_v2_fallback_routing, docs_zero_to_hero_study_handbook_credential_vault, docs_zero_to_hero_study_handbook_sqlite_store [EXTRACTED 1.00]

## Communities (197 total, 64 thin omitted)

### Community 0 - "Session Models and Tests"
Cohesion: 0.06
Nodes (64): Agent loop — the core orchestrator for the Computer Use engine. Delegates to…, Public model exports for tests and runtime imports., ActionType, AgentAction, AgentSession, load_allowed_models_json(), LogEntry, BaseModel (+56 more)

### Community 1 - "WebSocket Server API"
Cohesion: 0.04
Nodes (81): get_container_status(), is_container_running(), Return True if the named container is currently running., Return a dict with container running state and service health. The ``ready``…, agent_service_health(), api_agent_history(), api_agent_status(), api_engines() (+73 more)

### Community 2 - "Provider Run Contracts"
Cohesion: 0.06
Nodes (64): Any, EventCallback, SafetyCallback, Anthropic Computer Use provider loop. The public ``run`` function owns the…, Run Anthropic's native Computer Use loop with optional web/files., run(), emit_event(), maybe_plan_with_web_search() (+56 more)

### Community 3 - "Observability and Tracing"
Cohesion: 0.06
Nodes (51): Redact API-key-shaped tokens from free-form text., scrub_secrets(), assert_invariants(), bind_session_id(), _cli(), _default_trace_dir(), _digest(), drop_trace() (+43 more)

### Community 4 - "Agent Loop and API"
Cohesion: 0.07
Nodes (43): AgentLoop, Runs the perceive → think → act loop for a CUA session., Initialise a new agent loop for *task* using the given provider/model., Create a :class:`StructuredError`, append it to the error log, and return it., Invoke a callback, swallowing exceptions to keep the loop alive., _capture_v2_frame(), BaseModel, _RateLimiter (+35 more)

### Community 5 - "Request Models and Tests"
Cohesion: 0.06
Nodes (32): Validated request body for POST /api/agent/start., Uniform error envelope returned by the agent loop and executor. Every error…, Serialize to a plain dict for JSON responses., StartTaskRequest, StructuredError, CompletedProcess, models(), models_data() (+24 more)

### Community 6 - "Desktop Action Dispatch"
Cohesion: 0.06
Nodes (47): _blocked_cmd_match(), _open_terminal(), Move the mouse to (x,y) without clicking., Middle-click at (x,y) via xdotool., Copy the current selection to clipboard via xdotool., Gracefully close a window via EWMH using wmctrl -c., Capture the full screen via scrot., Capture a region of the screen via scrot. (+39 more)

### Community 7 - "Claude and Engine Core"
Cohesion: 0.08
Nodes (40): ClaudeCUClient, Native Claude computer-use tool protocol. API contract (as of 2026-04): -…, _gemini_final_needs_computer_use(), Gemini Computer Use client — split out of ``backend.engine`` (Q2). The class…, Return True when a UI task ended before any computer_use action., # IMPORTANT: send ONLY FunctionResponse parts — no separate image Part, _collect_transient_error_types(), Environment (+32 more)

### Community 8 - "Engine Validation Tests"
Cohesion: 0.07
Nodes (14): ComputerUseEngine, Single entry point for native Computer Use across providers and environments.…, Provider-specific completion payload for the most recent run., patch, Test ClaudeCUClient tool configuration., Any value other than exactly '1' keeps caching off., TestClaudeCachingEnvFlag, TestClaudeToolConfig (+6 more)

### Community 9 - "REST Origin and Auth"
Cohesion: 0.10
Nodes (43): api_agent_safety_confirm(), api_build_image(), api_delete_file(), api_screenshot(), api_start_agent(), api_start_container(), api_stop_agent(), api_stop_container() (+35 more)

### Community 10 - "React Workbench UI"
Cohesion: 0.08
Nodes (28): api, ApiError, AnalyticsPage(), App(), AuditPage(), LivePage(), ProvidersPage(), RouteBoundary (+20 more)

### Community 11 - "Engine Capability Schema"
Cohesion: 0.07
Nodes (41): keyboard, mouse, navigation, scroll, special, wait, allowed_actions, categories (+33 more)

### Community 12 - "Claude Client Tests"
Cohesion: 0.06
Nodes (25): _capture_create_kwargs(), client(), executor(), _minimal_png(), _png_bytes(), fixture, Sonnet 4.6 must send ``computer_20251124`` + ``computer-use-2025-11-24``, never…, Zoom is a computer_20251124-era action — Sonnet 4.6 must advertise it alongside… (+17 more)

### Community 13 - "Gemini Client and Tests"
Cohesion: 0.08
Nodes (25): _extract_gemini_grounding_payload(), GeminiCUClient, Any, TurnEvent, Native Gemini Computer Use tool protocol. API contract: - Declares…, Invoke Gemini generate_content via the native async SDK path. ``google-genai >=…, Return the SDK environment constant per official docs. Always reports…, Build the GenerateContentConfig with CU tools, safety, and thinking settings. (+17 more)

### Community 14 - "Async Engine Test Helpers"
Cohesion: 0.09
Nodes (13): asyncio, When scaling is active, coordinates should be upscaled., Claude adapter's zoom dispatch must call the executor with the validated region…, An inverted region must fail-fast without calling the executor — no crash, no…, Drive one turn through ``iter_turns`` and capture the…, Test _execute_claude_action for all supported Claude actions., Opus 4.7 with CUA_OPUS47_HIRES=1 at 2560x1600 keeps 1:1 coordinates — default…, Long edge > 2576 still clamps — the flag only drops the pixel-count cap, not… (+5 more)

### Community 15 - "Claude Provider Client"
Cohesion: 0.07
Nodes (27): _anthropic_web_search_cache_key(), _anthropic_web_search_error_message(), _anthropic_web_search_probe_lock(), _claude_caching_on(), _extract_claude_sources(), _is_anthropic_web_search_enablement_error(), Any, Lock (+19 more)

### Community 16 - "Action Execution Contract"
Cohesion: 0.09
Nodes (21): Map Claude computer tool actions to executor calls. Claude actions…, Handle double_click, right_click, triple_click, and middle_click., Convert SDK objects or typed dict-like values into a plain dict., _to_plain_dict(), _extract_openai_sources(), _openai_action_is_progress(), Any, A screenshot request is normal context gathering, not UI progress. (+13 more)

### Community 17 - "V2 API and Storage"
Cohesion: 0.21
Nodes (31): analytics(), compile_workflow(), create_credential_session(), create_session(), create_workflow(), create_workflow_version(), delete_credential_session(), delete_session() (+23 more)

### Community 18 - "Engine Fake Clients"
Cohesion: 0.09
Nodes (15): Run-loop must carry ``reasoning.encrypted_content`` and must never attempt…, TestOpenAIZDRReplay, _FakeExecutor, asyncio, A single computer_call with three actions should: * Dispatch all three actions…, If a computer_call has `.action` but no `.actions`, that single action should…, OpenAI docs allow a screenshot-first turn, but that is only observation. A…, If the model ignores the nudge and still returns only a generic final answer,… (+7 more)

### Community 19 - "V2 Execution Routing"
Cohesion: 0.21
Nodes (23): ContractModel, CredentialSessionInput, ErrorEnvelope, FallbackRouteInput, BaseModel, SessionInput, SessionPatch, WorkflowCompileInput (+15 more)

### Community 20 - "Sqlite Store"
Cohesion: 0.12
Nodes (8): Checkpoint, _now(), Any, Path, SQLite WAL persistence for v2 sessions, audit records, metrics, and workflows., SqliteStore, WorkflowVersion, test_sqlite_store_persists_session_actions_events_metrics_and_workflow_versions()

### Community 21 - "Sanitize Openai Response Item"
Cohesion: 0.09
Nodes (16): _build_openai_computer_call_output(), _extract_openai_output_text(), Any, Collect assistant text blocks from a Responses API output list., Build the Responses API follow-up item for a computer call., Strip output-only fields before replaying Responses items statelessly., Build the action executor for this session. Unified Computer Use surface: a…, Execute a CU task end-to-end using the native tool protocol. Args: goal:… (+8 more)

### Community 22 - "Test Infra"
Cohesion: 0.13
Nodes (22): JsonFormatter, Inject the current ``session_id`` ContextVar into every LogRecord., Context manager that binds *session_id* for the duration of the block., Single-line JSON formatter with session-id correlation. Emits: timestamp…, session_context(), SessionIdFilter, StringIO, fixture (+14 more)

### Community 23 - "Test V2 Platform"
Cohesion: 0.15
Nodes (24): _canonical(), CanonicalAction, CanonicalActionType, parse_anthropic_action(), parse_gemini_action(), parse_openai_action(), ProtocolActionError, Any (+16 more)

### Community 24 - "Agent Handler"
Cohesion: 0.09
Nodes (21): BaseHTTPRequestHandler, AgentHandler, _cached_action_result(), _do_wait(), _is_action_enabled(), Sleep for *duration* seconds (clamped to 0.1–10s)., HTTP handler for the supported desktop automation mode., Redirect HTTP request logging to the module logger. (+13 more)

### Community 25 - "Compiler Options"
Cohesion: 0.07
Nodes (28): compilerOptions, allowJs, allowSyntheticDefaultImports, esModuleInterop, exactOptionalPropertyTypes, forceConsistentCasingInFileNames, isolatedModules, jsx (+20 more)

### Community 26 - "Config Components"
Cohesion: 0.09
Nodes (18): Config, Full HTTP URL for the in-container agent service., Runtime configuration — values come from env vars or runtime overrides., _FakeContent, _FakePart, The CU guide recommends 1440x900 for best coordinate accuracy. The project's…, No ``safety_settings`` attached when the env flag is unset — Google's Gemini-3…, Gemini wants a Chromium-class browser even though Noble's apt packages are… (+10 more)

### Community 27 - "Desktop Executor"
Cohesion: 0.15
Nodes (4): DesktopExecutor, Translate CU actions into agent_service ``/action`` calls., Convert raw coordinates to pixel values, denormalizing if needed., POST an action to the agent_service and return the JSON result.

### Community 28 - "Open Aicuclient"
Cohesion: 0.12
Nodes (8): OpenAICUClient, OpenAI Responses API computer-use client. Uses the built-in ``computer`` tool…, Compose provider instructions without changing the advertised tools., GPT-5.5 defaults to ``medium`` and accepts ``xhigh``., Regression guards for the April 2026 OpenAI reasoning-effort enum. GPT-5.5…, TestOpenAIReasoningEffort, TestToolShape, asyncio

### Community 29 - "Engine Openai"
Cohesion: 0.10
Nodes (25): build_image(), _ensure_agent_token(), get_state(), Docker container lifecycle management., Run a command as an explicit argument list (no shell interpretation)., Build the CUA Docker image from docker/Dockerfile., Start the CUA Docker container with Xvfb + agent service. Holds…, Inner start routine. Caller must hold :data:`_LIFECYCLE_LOCK`. (+17 more)

### Community 30 - "Executor Components"
Cohesion: 0.10
Nodes (23): default_openai_reasoning_effort_for_model(), get_model_capabilities(), _is_modern_opus(), True for models using the lean modern-Opus prompt variant (Opus 4.7/4.8).…, Return the allowed_models.json entry for *model_id* as a plain dict. A2: single…, Return the OpenAI reasoning default for a model slug, from registry metadata.…, _ensure_openai_ga_model_is_in_registry(), _openai_final_asks_for_task() (+15 more)

### Community 31 - "Test Open Aiscroll Clamp"
Cohesion: 0.12
Nodes (16): close_shared_executor_clients(), denormalize_x(), denormalize_y(), Desktop Computer Use executor. This module owns screenshot capture plus…, Close every shared httpx client. Wire into FastAPI shutdown., Convert Gemini normalized x (0-999) to a pixel coordinate., Convert Gemini normalized y (0-999) to a pixel coordinate., Gemini normalised coords → pixel conversion. (+8 more)

### Community 32 - "Docker Components"
Cohesion: 0.11
Nodes (14): asyncio, Response, Preserve successful long-running workflows: many turns whose actions vary…, A typical 200 px scroll must pass through unchanged — i.e. the old…, ``magnitude=0`` would be a no-op for xdotool; the lower bound of 1 keeps it as…, When |dx| > |dy| the action is horizontal; negative dx is left. Magnitude must…, C10: 5xx / error-payload responses should fall back to docker exec, but 401/403…, Build a Response that has its ``.request`` set so ``raise_for_status`` works… (+6 more)

### Community 33 - "Dev Components"
Cohesion: 0.18
Nodes (23): _bootstrap(), _clear_dev_ports(), _clear_port(), _compose_restart(), _dotenv_values(), _env_int(), _error(), _info() (+15 more)

### Community 34 - "Open Url In Browser"
Cohesion: 0.09
Nodes (24): _browser_minimal_env(), _dismiss_known_modals(), _expand_app_launch_candidates(), _open_url_in_browser(), _post_launch_normalize(), Open URL in a deterministic browser — avoids xdg-open first-run problems., Focus a window by name or class., Launch an application by command name. After a successful launch the new window… (+16 more)

### Community 35 - "Test Claude Web Search"
Cohesion: 0.12
Nodes (11): asyncio, fixture, parametrize, Tool version + beta header must track model class., ``computer_20251124`` rejects ``budget_tokens``; must send adaptive., Safety decision ``require_confirmation`` routes through ``on_safety``., Claude CU requests stay computer-only when web planning is enabled., TestClaudeThinkingMode (+3 more)

### Community 36 - "Files Components"
Cohesion: 0.11
Nodes (20): Provision the vector store and upload all attached files. Implements the…, Delete the per-session vector store at run-loop exit. Best-effort: a transient…, cleanup_openai_vector_store(), close_store(), delete_file(), prepare_anthropic_documents(), prepare_openai_file_search(), Any (+12 more)

### Community 37 - "File Store"
Cohesion: 0.15
Nodes (15): Validate provider/file-id compatibility and return deduped ids., validate_attached_files(), FileStore, Process-wide in-memory registry of uploads, persisted to disk., Remove an upload from disk + registry. Returns True on success., Sweep uploads older than ``UPLOAD_TTL_SECONDS``. Returns count removed., Wipe the entire upload root. Wired into FastAPI shutdown., _ChunkedUpload (+7 more)

### Community 38 - "Test Config"
Cohesion: 0.11
Nodes (13): _detect_key_source(), get_all_key_statuses(), KeyStatus, _mask_key(), Resolution status for a single provider's API key., Return a masked version of an API key for safe display., Detect where an API key comes from. Returns ``(key_value, source_label)``.…, Resolve the API key for *provider* using the priority chain. Priority: UI input… (+5 more)

### Community 39 - "Agent Service"
Cohesion: 0.10
Nodes (19): Resolve an action string to its canonical ActionType value. Returns the…, resolve_action(), _effective_allowed_commands(), _env_bool(), _is_safe_upload_path(), main(), Internal agent service — runs INSIDE the Docker container. Provides a…, Start the HTTP agent service for desktop automation. (+11 more)

### Community 40 - "Computer Use Workbench Readme"
Cohesion: 0.14
Nodes (21): Computer Use Prompt Guide, Approval Boundary, Deployment Guide, Gemini Successor Evaluation Checklist, Gemini Successor Capability Gate, v1 to v2 Migration Guide, v2.0.0 Release Notes, Computer Use Model and Platform Audit (+13 more)

### Community 41 - "Make Components"
Cohesion: 0.15
Nodes (4): OpenAI CU requests stay computer-only when web planning is enabled., Gemini CU requests stay computer-only when web planning is enabled., TestGeminiGoogleSearch, TestOpenAIWebSearch

### Community 42 - "Test Browser Security Posture"
Cohesion: 0.10
Nodes (12): OpenAI CU guide's browser hardening contract: when the agent spawns a Chromium-…, Regression guard: ``--disable-file-system`` protects the sandbox if the…, The browser subprocess must NOT inherit the full host env (it would leak…, Source-scan regression guard: no ``env={**os.environ, ...}`` on the browser…, OpenAI CU guide Option 1 mandates XFCE4 as the WM., Light-locker / xfce4-screensaver / xfce4-power-manager steal focus from xdotool…, Regression guard: S1's shared viewport survives. OpenAI's reference Dockerfile…, ``_build_openai_computer_call_output`` is the single code path that packs… (+4 more)

### Community 43 - "Test Gap Coverage"
Cohesion: 0.10
Nodes (13): Targeted coverage tests for previously-untested code paths. Covers the…, If the agent loop raises, the session must be marked ERROR, the finish event…, A non-HTTP error inside the shared publisher must trigger backoff., The source of ``/api/agent/start`` must await _broadcast then call cleanup.…, A timeout inside the shared publisher must trigger backoff., The 60 s wait_for path must return False (deny) on TimeoutError. Captures the…, TestDockerfileLayerSplit, TestEntrypointServiceVerification (+5 more)

### Community 44 - "Engine Capabilities"
Cohesion: 0.12
Nodes (14): EngineCapabilities, Schema version string., All registered engine names., Return the set of allowed actions for *engine_name*. Returns an empty frozenset…, Return ``True`` if *action* is valid for *engine_name*. This is the primary…, Validate and return a ``(ok, message)`` tuple. On failure the message explains…, Machine-readable engine capability registry. Parameters: schema_path: Path to…, Validate that: 1. All engine actions in engine_capabilities.json exist in… (+6 more)

### Community 45 - "Gemini Changelog Watchdog"
Cohesion: 0.18
Nodes (14): HTMLParser, build_failure_message(), _extract_announcement_block(), fetch_changelog_html(), find_shutdown_announcement(), _find_shutdown_in_section(), html_to_lines(), _is_date_heading() (+6 more)

### Community 46 - "Test Engine"
Cohesion: 0.13
Nodes (10): _lookup_claude_cu_config(), Look up cu_tool_version / cu_betas from allowed_models.json. Returns…, Test _lookup_claude_cu_config reads from allowed_models.json., Verify pruning constant matches reference implementations., Should match Google reference MAX_RECENT_TURN_WITH_SCREENSHOTS and Anthropic…, Provider-specific native iterators should be used when available., TestContextPruneConstant, TestIterTurnsDispatch (+2 more)

### Community 47 - "Get Components"
Cohesion: 0.12
Nodes (9): _FakeHeaders, parametrize, /vnc/{path} should reject traversal + non-whitelisted paths and surface…, /api/v1/* must route to the same handlers as /api/*., Whitelisted path but upstream websockify down → 502., Happy path: vnc.html returns upstream bytes + content-type., TestApiV1Alias, TestComposeHardening (+1 more)

### Community 48 - "Engine Certifier"
Cohesion: 0.12
Nodes (10): EngineCertifier, Path, Engine and tool validation framework. Loads ``engine_capabilities.json`` and…, Verify top-level schema structure and required fields per engine., Verify all engines are properly registered., Return list of missing binary names for the given engine., Verify categories <-> allowed_actions parity for one engine., Run a safe, non-destructive execution probe for *engine_name*. Returns one of:… (+2 more)

### Community 49 - "Xdo Type Text At"
Cohesion: 0.12
Nodes (18): _is_terminal_focused(), _map_key_combo_xdotool(), Check if the currently focused window is a terminal emulator. Terminals…, Copy text to clipboard then paste via Ctrl+V (or Ctrl+Shift+V in terminals)., Press a multi-key combo via xdotool., Map a user key string to an xdotool key combo., Click at (x,y) via xdotool., Composite "type at coordinate": click → optionally clear → type → optionally… (+10 more)

### Community 50 - "Test Agent Start Validation"
Cohesion: 0.12
Nodes (5): Legacy ``mode="browser"`` is accepted for wire compatibility but is now ignored…, Any value supplied for the legacy ``mode`` field is accepted and ignored under…, Without any API key source, should get a clear error., Test input validation on POST /api/agent/start., TestAgentStartValidation

### Community 51 - "Get System Prompt"
Cohesion: 0.16
Nodes (9): get_system_prompt(), Any, Return the system prompt for the computer_use engine. Parameters ----------…, Negative control: non-4.7 Claude models still benefit from the scaffolding and…, Opus 4.6 (computer_20251124 tool but older reasoning) still benefits from…, Callers that don't pass a model id must get the conservative scaffolded prompt., TestPromptAudit, parametrize (+1 more)

### Community 52 - "Dev Dependencies"
Cohesion: 0.13
Nodes (15): eslint, devDependencies, eslint, @testing-library/dom, @testing-library/react, @testing-library/user-event, typescript, @vitejs/plugin-react (+7 more)

### Community 53 - "Test Vnc Websockify Token"
Cohesion: 0.21
Nodes (6): When ``CUA_WS_TOKEN`` is set, /vnc/websockify must reject upgrades without a…, Build a MagicMock WebSocket with the given ?token=... and Origin.…, Success criterion (1): CUA_WS_TOKEN set + no token → close(4401) BEFORE accept…, Success criterion (2): CUA_WS_TOKEN set + matching ?token= → proxy proceeds…, Success criterion (3): CUA_WS_TOKEN unset → no token required on either…, TestVncWebsockifyTokenGating

### Community 54 - "Prune Claude Context"
Cohesion: 0.22
Nodes (9): _prune_claude_context(), Replace base64 screenshot data in old turns with a placeholder. Keeps the first…, Verify _prune_claude_context replaces old screenshots with placeholders., Build a realistic Claude message list with n tool_result pairs., Messages shorter than keep_recent should not be pruned., Old tool_result images should become [screenshot omitted]., The most recent keep_recent messages should retain screenshots., The first user message always keeps its screenshot. (+1 more)

### Community 55 - "Runtime Error"
Cohesion: 0.16
Nodes (12): _agent_headers(), capture_screenshot(), check_service_health(), _fallback_docker_screenshot(), _get_client(), AsyncClient, Return a reusable agent-service HTTP client for lightweight probes., Grab a screenshot with docker exec + scrot when the service is unhealthy. (+4 more)

### Community 56 - "Safety Components"
Cohesion: 0.21
Nodes (7): TestClient, parametrize, MonkeyPatch, Even when the container is stopped / unknown, liveness stays up. This is the…, `/api/ready` returns 200 only when the backend can start a session., Both the docker probe AND the key check fail → both reasons surface. Operators…, TestReadiness

### Community 57 - "Test Client"
Cohesion: 0.20
Nodes (12): agent_service(), agent_service_legacy(), dockerfile(), entrypoint(), _load_agent_service(), fixture, Import docker/agent_service.py as a standalone module. Mirrors the loader in…, Load the module with legacy actions enabled. ``run_command`` is intentionally… (+4 more)

### Community 58 - "Test Agent Service"
Cohesion: 0.20
Nodes (8): Drive ``_dispatch_desktop`` through the ``run_command`` branch with a stub…, Invoke the ``run_command`` branch with minimal plumbing., Baseline: the allowlist still denies ``curl`` (not in allow-set). Establishes…, Executable IS on the allowlist (``bash`` isn't, use ``python3``), but the argv…, Trivial casing tricks must not bypass the gate., Gate-type leakage check: the error payload for a blocked pattern on an…, Regression guard for criterion #2: a request that passes both gates must still…, TestRunCommandEnforcement

### Community 59 - "Test Run Command Enforcement"
Cohesion: 0.21
Nodes (5): FakeExecutor, Minimal executor stub used to verify Claude action translation., _png_bytes(), Verify the OpenAI runtime loop sends the expected follow-up payloads., TestOpenAIRuntimePath

### Community 60 - "Fake Executor"
Cohesion: 0.15
Nodes (7): Scaling factor computation per Anthropic docs., Screens that fit within both thresholds → scale = 1.0., Default 1440×900 (1.296M pixels) exceeds pixel threshold., 3840×2160 exceeds both edge and pixel limits., Screen exactly at max long edge and pixel limit., Verify the scale factor matches the documented formula., TestClaudeScaleFactor

### Community 61 - "Test Claude Scale Factor"
Cohesion: 0.15
Nodes (3): Business rules for model allowlist., Anthropic models with CU support must declare tool version and betas., TestModelPolicy

### Community 62 - "Test Model Policy"
Cohesion: 0.21
Nodes (7): get_claude_scale_factor(), Return True when the Claude run should use the 2025-11-24 CU path. Prefer the…, Compute Anthropic screenshot scale factor per official docs. Returns a factor…, _uses_claude_20251124(), ``computer_20251124`` models are 1:1 at typical resolutions., TestClaudeCoordinateSpace, TestClaudeScaleFactorOpus47

### Community 63 - "Get Claude Scale Factor"
Cohesion: 0.24
Nodes (5): Reject names containing shell metacharacters., _validate_name(), Verify docker_manager input validation and security flags., Verify the source code includes --security-opt and resource limits., TestDockerManagerSecurity

### Community 64 - "Validate Name"
Cohesion: 0.18
Nodes (8): EngineSchema, get_default_schema_path(), Any, Path, Return the full ``EngineSchema`` for *engine_name*, or ``None``., Return the package-relative capability schema path., Typed representation of a single engine's capability block., Parse a single engine's JSON block into typed fields.

### Community 65 - "Engine Schema"
Cohesion: 0.18
Nodes (11): arm(), clear(), get_or_create_event(), Return an existing event for the session or create a fresh one., Arm a fresh safety prompt for *session_id*; return ``(nonce, event)``. Creates…, Constant-time check that *supplied* matches the session's armed nonce., Signal an already-armed event. Returns False if the session isn't armed. Unlike…, Drop any pending event, decision, and nonce for the session. (+3 more)

### Community 66 - "Models Components"
Cohesion: 0.20
Nodes (7): _CatalogDocument, ModelCatalog, ModelRoute, BaseModel, Path, Validated, transport-aware Computer Use model catalog., test_model_catalog_is_transport_aware_and_computer_use_only()

### Community 67 - "Test Blocked Cmd Match"
Cohesion: 0.17
Nodes (6): Pure-function tests: _blocked_cmd_match must look at the whole argv, not just…, argv[0] alone is benign; the dangerous phrase hides in a later arg. The…, Pattern-matching must survive trivial ``SHUTDOWN`` / ``Rm -Rf /`` casing tricks., ``:(){`` is the classic fork-bomb prefix., Negative control: an ordinary ``ls /tmp`` must not trip., TestBlockedCmdMatch

### Community 68 - "Value Error"
Cohesion: 0.20
Nodes (9): Persist an uploaded file by reading from an async stream in chunks., upload_file_stream(), extract_text(), Return the metadata for *file_id* or ``None`` if unknown., Return the file's textual content for inline injection. Used only by the…, One server-side upload. Bytes live on disk at ``path``., Load the persisted bytes back from disk., UploadedFile (+1 more)

### Community 69 - "Add Stream"
Cohesion: 0.24
Nodes (7): _mime_for(), Path, Persist *data* as a new uploaded file. Raises: ValueError: extension/size/magic…, Persist an uploaded file by reading it in chunks. This avoids materializing the…, Return the MIME type expected by the provider APIs for *ext*., Cross-check magic bytes for binary formats., _validate_magic()

### Community 70 - "Engine Report"
Cohesion: 0.22
Nodes (7): EngineReport, Any, Certification result for a single engine., Serialise to a JSON-friendly dict., Check binary and env-var deps for one engine., Populate *report* with any missing binary dependencies from *reqs*., Populate *report* with any missing environment variables from *reqs*.

### Community 71 - "Credential Vault"
Cohesion: 0.22
Nodes (6): CredentialSession, CredentialVault, BaseModel, Process-local credential sessions; secrets are never persisted or serialized., SecretStr, test_credential_vault_never_serializes_secrets_and_expires()

### Community 72 - "Frame Retention Store"
Cohesion: 0.25
Nodes (3): FrameRetentionStore, Path, Bounded audit-frame retention with age and byte-budget eviction.

### Community 73 - "Test Action Gate"
Cohesion: 0.20
Nodes (4): _make_post_handler(), Construct a minimal POST /action handler and capture its response., ``_is_action_enabled`` is the single source of truth the handler consults…, TestActionGate

### Community 74 - "Fake Executor"
Cohesion: 0.20
Nodes (5): _FakeExecutor, Anything shorter than 100 B is treated as a capture failure., Fake executor that returns a caller-controlled screenshot once., Empty screenshot bytes must terminate cleanly with an error log and a…, TestClaudeInitialScreenshotGuard

### Community 75 - "Test Agent Handler Auth"
Cohesion: 0.25
Nodes (5): _make_handler(), Construct an AgentHandler without running the BaseHTTPRequestHandler init., _authorized must honour the X-Agent-Token header and exempt /health., Sanity: token comparison is via hmac.compare_digest (no plain ==)., TestAgentHandlerAuth

### Community 76 - "Test Env Clamping"
Cohesion: 0.29
Nodes (4): S3 — numeric env values must be clamped to safe ranges., Build a Config under a fully-scrubbed env so existing vars don't leak in., MAX_STEPS must respect the 200-step hard cap enforced upstream., TestEnvClamping

### Community 77 - "Resize Screenshot For Claude"
Cohesion: 0.31
Nodes (5): Resize a PNG screenshot by *scale* factor. Returns (resized_png_bytes,…, resize_screenshot_for_claude(), Screenshot resize via Pillow., Create a minimal PNG of given size using Pillow., TestResizeScreenshot

### Community 78 - "Configure Logging"
Cohesion: 0.22
Nodes (8): configure_logging(), install(), Logger, Attach the :class:`SessionIdFilter` to every handler on *root_logger*.…, Install the session-id filter and (optionally) a JSON formatter. Call once at…, MonkeyPatch, An unrecognised LOG_FORMAT must not crash startup., Two calls back-to-back are safe (no duplicate filters).

### Community 79 - "Validation Components"
Cohesion: 0.29
Nodes (7): CertificationReport, main(), _print_table(), Aggregate certification result for all engines., Serialise to a JSON-friendly dict., Execute all validation phases and return an aggregate report., CLI entry point for ``python -m backend.models.validation``.

### Community 80 - "Entrypoint Sh"
Cohesion: 0.20
Nodes (9): DISPLAY, HEIGHT, PATH, PYTHONPATH, SCREEN_DEPTH, SCREEN_HEIGHT, SCREEN_WIDTH, entrypoint.sh script (+1 more)

### Community 81 - "Test Container Readiness Gating"
Cohesion: 0.20
Nodes (6): The previous behaviour returned success from ``_wait_for_service`` whenever the…, Container process survives but /health always errors → False, and get_state()…, Happy path: a 200 from /health flips state to ready and clears any prior…, End-to-end: when the cached readiness says the sandbox isn't ready, POST…, If ``start_container()`` fails because an already-running container never…, TestContainerReadinessGating

### Community 82 - "Test Fixes Wave Apr2026"
Cohesion: 0.20
Nodes (5): _FakeMessageStream, _minimal_png(), Tests for the April 2026 adapter-alignment wave. Covers: * Fix 3: Claude…, Async-CM stand-in for ``client.beta.messages.stream(...)`` (D2)., Return bytes large enough to pass the >=100 B guard.

### Community 83 - "From Env"
Cohesion: 0.22
Nodes (7): _clamp_float(), _clamp_int(), _env_bool(), Create a Config instance from environment variables. Numeric values read from…, Read ``var`` as int, falling back to ``default``, then clamp to [lo, hi]. Non-…, Read ``var`` as a boolean-like env override, falling back to ``default``., Read ``var`` as float with the same clamping semantics as :func:`_clamp_int`.

### Community 84 - "Dependencies Components"
Cohesion: 0.22
Nodes (9): dependencies, lucide-react, react, react-dom, react-router-dom, lucide-react, react, react-dom (+1 more)

### Community 85 - "Scripts Components"
Cohesion: 0.22
Nodes (9): scripts, build, coverage, dev, lint, preview, test, test:run (+1 more)

### Community 86 - "Test Key Allowlist"
Cohesion: 0.22
Nodes (4): C8: model-emitted key tokens are restricted to an explicit allowlist., C8 follow-up: ``hold_key`` must enforce the same allowlist as…, Preserve normal supported behavior: ``Shift`` held for a second is the…, TestKeyAllowlist

### Community 87 - "Test Fix Pass Remediation"
Cohesion: 0.33
Nodes (4): client(), _FakeLoop, fixture, TestSafetyConfirmAuthz

### Community 88 - "Test Upload Path Containment"
Cohesion: 0.22
Nodes (4): The previous implementation used ``str.startswith(root + os.sep)`` which is…, ``/tmpX/...`` must NOT be accepted just because ``/tmp`` is allowed., Behaviour preserved from the prefix-string version: the root directory itself…, TestUploadPathContainment

### Community 89 - "Prune Gemini Context"
Cohesion: 0.50
Nodes (3): _prune_gemini_context(), Drop old Gemini history turns atomically while keeping kept turns intact.…, TestGeminiHistoryPruning

### Community 90 - "Config Components"
Cohesion: 0.29
Nodes (6): Application configuration with environment-based settings., _enforce_public_bind_guardrail(), main(), Entry point for the backend server., Refuse to start when binding externally without the WS auth token. The REST +…, Launch the FastAPI backend via Uvicorn.

### Community 91 - "Validate Outbound"
Cohesion: 0.36
Nodes (4): Any, Validate a dict payload against the registered event schema. Returns ``None``…, validate_outbound(), TestWsSchemaValidation

### Community 93 - "Test Start Container"
Cohesion: 0.25
Nodes (3): When _wait_for_service returns False we must docker rm the half-started…, Unit tests for the fresh-run + teardown branches without touching docker., TestStartContainer

### Community 94 - "Vnc Http Proxy"
Cohesion: 0.29
Nodes (7): _get_novnc_client(), _is_safe_vnc_path(), AsyncClient, Return a reusable httpx client for noVNC proxying., Reject traversal, absolute paths, encoded slashes; enforce whitelist., Proxy noVNC static files from the container's websockify web server., vnc_http_proxy()

### Community 95 - "V2 Orchestrator"
Cohesion: 0.29
Nodes (3): V2Orchestrator, ExecutionStarter, Task

### Community 96 - "Build Docs Site"
Cohesion: 0.43
Nodes (6): main(), Build a static, multipage htmx site from every tracked .md file in the repo.…, Point in-doc links to other tracked .md files at their generated page., rewrite_md_links(), slug_for(), title_for()

### Community 98 - "Agent Service"
Cohesion: 0.29
Nodes (7): agent_service(), _load_agent_service_module(), fixture, Load docker/agent_service.py as a standalone module (it's not on sys.path)., Import docker/agent_service.py once for the test module., TestClient over backend.server.app for the /vnc proxy assertions., server_client()

### Community 99 - "Client Components"
Cohesion: 0.29
Nodes (7): clear_server_rate_limiters(), client(), fixture, Create a FastAPI TestClient for backend.server.app., Import backend.server with the publisher's IO stubbed out. We patch: *…, Keep per-process rate limiters from leaking state across tests., server_mod()

### Community 100 - "Cua Computer Using Agent"
Cohesion: 0.33
Nodes (6): Agent Action Timeline, CUA Computer Using Agent, GPT-5.5 Model, CUA Computer Using Agent Interface, Manual API Key Source, Agent Step Limit

### Community 101 - "Capture Screenshot"
Cohesion: 0.33
Nodes (3): Return the shared-secret header for authenticated agent_service calls., Capture a screenshot via the agent_service, with docker exec fallback., Grab a screenshot via ``docker exec scrot`` as last resort.

### Community 102 - "Mount Production Frontend"
Cohesion: 0.47
Nodes (6): _mount_production_frontend(), Path, Mount a Vite production build when present; remain non-fatal in dev., FastAPI, test_production_frontend_mount_is_optional(), test_production_frontend_mount_serves_assets_and_spa_routes()

### Community 103 - "Test Ready Agent Does"
Cohesion: 0.40
Nodes (4): ``POST /api/agent/start`` must refuse when the sandbox is unready., Sanity check: same payload, ``agent=ready`` → not a 409., _start_payload(), TestDegradedContainerStartup

### Community 104 - "Build Release"
Cohesion: 0.53
Nodes (5): copy(), main(), Path, Build reproducible v2 release assets without publishing them., run()

### Community 105 - "Setup Sh"
Cohesion: 0.60
Nodes (5): error(), info(), setup.sh script, usage(), warn()

### Community 108 - "Capture Thinking"
Cohesion: 0.53
Nodes (3): asyncio, Current-tool Claude models use adaptive thinking; legacy models keep the fixed-…, TestClaudeThinkingMode

### Community 109 - "Test Engine Package Split"
Cohesion: 0.33
Nodes (3): __init__.py should be well below the pre-split 1992 lines (Q2)., Public names still importable from the package root., TestEnginePackageSplit

### Community 110 - "Execute Components"
Cohesion: 0.40
Nodes (4): Any, Return the post-action settle delay appropriate for action *name*., Map a CU action to the agent_service ``/action`` endpoint., _settle_delay_for()

### Community 112 - "Allowed Models"
Cohesion: 0.40
Nodes (4): description, models, $schema, version

### Community 113 - "Computer Use Models V2"
Cohesion: 0.40
Nodes (4): models, $schema, verified_at, version

### Community 114 - "Agent Loop"
Cohesion: 0.40
Nodes (5): AgentLoop, Perceive Think Act Agentic Loop, ComputerUseEngine, V2Orchestrator, V2 Shared Execution Core Rationale

### Community 115 - "Package Components"
Cohesion: 0.40
Nodes (4): name, private, type, version

### Community 116 - "Test Oci Labels"
Cohesion: 0.40
Nodes (3): parametrize, Version / revision / created must be ARG-backed so a release pipeline can stamp…, TestOciLabels

### Community 117 - "Test Signal Clean Shutdown"
Cohesion: 0.40
Nodes (3): ``exec python ...`` as the last meaningful line means the Python process…, ``docker/agent_service.py`` must register SIGTERM/SIGINT handlers so ``docker…, TestSignalCleanShutdown

### Community 119 - "Text Block"
Cohesion: 0.40
Nodes (3): Stand-in for anthropic response text content blocks., TestClaudeRefusalBranch, _TextBlock

### Community 124 - "Action Id Filter"
Cohesion: 0.50
Nodes (3): _ActionIdFilter, LogRecord, Inject the current request's action_id onto every LogRecord.

### Community 125 - "Xdo Type"
Cohesion: 0.50
Nodes (4): Type text via xdotool with modifier-key safety. Strategy: 1. Try ``xdotool…, Send *text* one character at a time via ``xdotool key``. This bypasses the…, _xdo_type(), _xdo_type_key_per_char()

### Community 126 - "Offline Deterministic Evals"
Cohesion: 0.50
Nodes (4): Computer Use Prompt Guide, Offline Deterministic Evals, Operator Usage Guide, Technical Architecture Documentation

### Community 127 - "Clear Active Registries"
Cohesion: 0.50
Nodes (4): _clear_active_registries(), client(), fixture, Reset the active-session registries before each eval run.

### Community 138 - "Container Hardening Posture"
Cohesion: 0.67
Nodes (3): Docker Compose Configuration, Docker Security Notes, Container Hardening Posture

### Community 139 - "Desktop Executor"
Cohesion: 0.67
Nodes (3): In-container Agent Service, Provider Coordinate Spaces, DesktopExecutor

### Community 140 - "Study Handbook"
Cohesion: 1.00
Nodes (3): HTML Study Handbook, PDF Study Handbook, Zero to Hero Study Handbook: computer-use

## Knowledge Gaps
- **144 isolated node(s):** `$schema`, `version`, `description`, `models`, `$schema` (+139 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **64 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_xdo()` connect `Desktop Action Dispatch` to `Open Url In Browser`, `Agent Service`, `Xdo Type Text At`, `Runtime Error`, `Xdo Type`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Why does `ClaudeCUClient` connect `Claude and Engine Core` to `Provider Run Contracts`, `Test Claude Web Search`, `Engine Validation Tests`, `Fake Executor`, `Test Gap Coverage`, `Claude Client Tests`, `Gemini Client and Tests`, `Async Engine Test Helpers`, `Claude Provider Client`, `Action Execution Contract`, `Test Engine`, `Test Fixes Wave Apr2026`, `Engine Fake Clients`, `Capture Thinking`, `Text Block`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Why does `OpenAICUClient` connect `Open Aicuclient` to `Session Models and Tests`, `Provider Run Contracts`, `Observability and Tracing`, `Agent Loop and API`, `Test Origin Gating`, `Claude and Engine Core`, `Engine Validation Tests`, `Gemini Client and Tests`, `Action Execution Contract`, `Engine Fake Clients`, `Executor Components`, `Docker Components`, `Files Components`, `Test Gap Coverage`, `Test Engine`, `Test Vnc Websockify Token`, `Test Run Command Enforcement`, `Test Container Readiness Gating`, `Test Key Allowlist`, `Test Public Bind Guardrail`, `Test Web Socket Origin`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Are the 112 inferred relationships involving `patch` (e.g. with `.test_ready_agent_does_not_409()` and `.test_unready_agent_returns_409_and_no_session_row()`) actually correct?**
  _`patch` has 112 INFERRED edges - model-reasoned connections that need verification._
- **Are the 29 inferred relationships involving `OpenAICUClient` (e.g. with `ComputerUseEngine` and `CUTurnRecord`) actually correct?**
  _`OpenAICUClient` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 50 inferred relationships involving `AgentLoop` (e.g. with `ActionType` and `AgentAction`) actually correct?**
  _`AgentLoop` has 50 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `ClaudeCUClient` (e.g. with `ActionExecutor` and `CUActionResult`) actually correct?**
  _`ClaudeCUClient` has 11 INFERRED edges - model-reasoned connections that need verification._