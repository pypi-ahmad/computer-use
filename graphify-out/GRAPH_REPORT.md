# Graph Report - D:\AI\Github\computer-use  (2026-08-12)

## Corpus Check
- 184 files · ~404,321 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2559 nodes · 5173 edges · 173 communities (131 shown, 42 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 759 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Tests Session
- Backend Server Return
- Backend Providers Run
- Tests Models
- Backend Server Api
- Backend Engine Gemini
- Tests Engine Default
- Docker Xdo
- Tests Effort
- Frontend Src Api
- Tests Engine Scale
- Backend V2 Session
- Backend Engine Search
- Backend Infra Trace
- Backend Models Scroll
- Backend Server Events
- Tests Agent
- Tests Engine Sonnet46
- Tests Engine Opus47
- Backend Engine Openai
- Backend V2 Fallback
- Backend Infra Key
- Project Codebase
- Tests Only
- Backend Infra Container
- Tests Token
- Tests Session
- Backend V2 Session
- Docker Action
- Frontend Dom
- Backend Act
- Project Port
- Tests Engine Tool
- Backend V2 Parse
- Backend Store
- Backend Infra Bytes
- Docker Window
- Tests Engine Only
- Backend Engine Loop
- Tests Upload
- Tests Engine Gemini
- Tests Engine Openai
- Tests Bytes
- Tests Openai
- Backend Models Engine
- Docker Window
- Scripts Shutdown
- Tests Key
- Docs Zero
- Tests Engine Prompt
- Backend V2 Credential
- Backend Engine Execute
- Backend Back
- Tests Response
- Tests Rejected
- Backend Engine Model
- Backend Task
- Backend Models Engine
- Frontend Testing
- Scripts Build
- Tests Docker Agent
- Tests Token
- Tests Engine Screenshot
- Backend V2 Execution
- Docker Key
- Tests Ready
- Tests Docker Command
- Tests Engine Loop
- Backend Screenshot
- Backend V2 Retention
- Tests Capable
- Tests Engine Gemini
- Tests Docker Validate
- Backend Models Engine
- Backend Event
- Backend V2 Models
- Tests Docker Match
- Backend Scrub
- Backend Models Check
- Tests Docker Action
- Tests Clamped
- Tests Screenshot
- Docker Screen
- Tests Container
- Backend Client
- Frontend React
- Frontend Build
- Tests Key
- Tests Executor
- Tests Nonce
- Tests Rejected
- Tests Handbook
- Tests Engine Turn
- Backend Models Validation
- Backend Prompt
- Tests Event
- Tests Bind
- Tests Docker
- Scripts Build
- Tests Origin
- Tests Server
- Assets Agent
- Evals Agent
- Scripts Build
- Project Setup
- Tests Transient
- Tests Should
- Backend Action
- Backend Infra Session
- Backend Models Allowed
- Backend Models Computer
- Frontend Package
- Tests Docker Version
- Tests Docker Exec
- Tests Engine Width
- Tests Engine Single
- Tests Engine Uses
- Backend Key
- Backend Infra Return
- Docker Logrecord
- Docker Terminal
- Docker Type
- Evals Guide
- Evals Active
- Tests Docker Blocked
- Tests Docker Agent
- Tests Docker Healthcheck
- Tests Docker Engine
- Tests Docker Resolve
- Tests Docker Magnitude
- Tests Engine Reference
- Backend Engine Collect
- Backend Infra Bind
- Docker Compose
- Tests Engine Provider
- Tests Concurrent
- Backend Aclose
- Backend Url
- Backend V2 Computer
- Codex Better Harness Task
- Project Dev
- Docs Computer
- Docs Rollback
- Frontend Eslint
- Frontend Eslint
- Frontend Eslint
- Evals Deterministic
- Evals Container
- Frontend Cua
- Frontend Jsdom
- Frontend Testing
- Frontend Types
- Frontend Types
- Frontend Typescript
- Frontend Vite
- Frontend Vitest
- Tests Engine Testdesktopexecutoridempotency
- Github Workflows Workflow
- Github Workflows Gemini
- Github Workflows Release
- Project Computer

## God Nodes (most connected - your core abstractions)
1. `OpenAICUClient` - 86 edges
2. `AgentLoop` - 69 edges
3. `ClaudeCUClient` - 64 edges
4. `DesktopExecutor` - 61 edges
5. `SessionStatus` - 59 edges
6. `GeminiCUClient` - 57 edges
7. `AgentSession` - 54 edges
8. `CUActionResult` - 52 edges
9. `ActionType` - 43 edges
10. `SqliteStore` - 38 edges

## Surprising Connections (you probably didn't know these)
- `OpenAI Anthropic and Gemini Routes` --semantically_similar_to--> `Provider-Native Execution Routes`  [INFERRED] [semantically similar]
  USAGE.md → README.md
- `Dashboard v2 Surface` --semantically_similar_to--> `Typed v2 API Contract`  [INFERRED] [semantically similar]
  USAGE.md → README.md
- `Safety Confirmations` --semantically_similar_to--> `Single-User Safety Boundary`  [INFERRED] [semantically similar]
  USAGE.md → README.md
- `Isolated Disposable Virtual Desktop` --semantically_similar_to--> `Docker Sandbox`  [INFERRED] [semantically similar]
  USAGE.md → README.md
- `Dashboard v2 Surface` --semantically_similar_to--> `Five-Tab React Dashboard`  [INFERRED] [semantically similar]
  USAGE.md → README.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **v2 Release Assurance** — github_workflows_ci, github_workflows_release [INFERRED 0.85]
- **Workbench Architecture** — readme_fastapi_orchestration, readme_sqlite_audit_history, readme_docker_sandbox, readme_react_dashboard [EXTRACTED 1.00]
- **Safety Boundary Documentation** — security_security_policy, technical_safety_decision_policy, technical_process_local_credentials, docs_codebase_concerns_codebase_concerns [INFERRED 0.85]
- **Operator Safety Controls** — readme_safety_boundary, usage_safety_confirmations, usage_network_hardening, usage_credential_vault [INFERRED 0.85]
- **v2 operator lifecycle documentation** — docs_deployment_document, docs_migration_v2_document, docs_release_notes_v2_0_0_document [INFERRED 0.85]
- **handbook source and companion renditions** — docs_zero_to_hero_study_handbook_document, docs_zero_to_hero_study_handbook_html_document, docs_zero_to_hero_study_handbook_pdf_document [EXTRACTED 1.00]

## Communities (173 total, 42 thin omitted)

### Community 0 - "Tests Session"
Cohesion: 0.05
Nodes (64): Agent loop — the core orchestrator for the Computer Use engine. Delegates to…, AgentAction, AgentSession, LogEntry, Full state of an agent run., Structured log entry emitted over WebSocket., Structured action returned by the LLM., Lifecycle states for an agent session. (+56 more)

### Community 1 - "Backend Server Return"
Cohesion: 0.05
Nodes (77): close_shared_executor_clients(), Close every shared httpx client. Wire into FastAPI shutdown., agent_service_health(), api_agent_history(), api_agent_status(), api_engines(), api_keys_status(), api_models() (+69 more)

### Community 2 - "Backend Providers Run"
Cohesion: 0.06
Nodes (64): Any, EventCallback, SafetyCallback, Anthropic Computer Use provider loop. The public ``run`` function owns the…, Run Anthropic's native Computer Use loop with optional web/files., run(), emit_event(), maybe_plan_with_web_search() (+56 more)

### Community 3 - "Tests Models"
Cohesion: 0.05
Nodes (41): Public model exports for tests and runtime imports., ActionType, load_allowed_models_json(), BaseModel, str, Validated request body for POST /api/agent/start., Supported agent actions for the computer-use engine. Three categories: 1. CU-…, Response shape for GET /api/agent/status. (+33 more)

### Community 4 - "Backend Server Api"
Cohesion: 0.06
Nodes (60): api_agent_safety_confirm(), api_build_image(), api_delete_file(), api_screenshot(), api_shutdown_application(), api_start_agent(), api_start_container(), api_stop_agent() (+52 more)

### Community 5 - "Backend Engine Gemini"
Cohesion: 0.06
Nodes (17): ComputerUseEngine, Single entry point for native Computer Use across providers and environments.…, Provider-specific completion payload for the most recent run., patch, Test ClaudeCUClient tool configuration., Any value other than exactly '1' keeps caching off., TestClaudeCachingEnvFlag, TestClaudeToolConfig (+9 more)

### Community 6 - "Tests Engine Default"
Cohesion: 0.09
Nodes (41): _extract_gemini_grounding_payload(), _gemini_final_needs_computer_use(), GeminiCUClient, Any, Gemini Computer Use client — split out of ``backend.engine`` (Q2). The class…, Gemini Interactions API Computer Use client., Return the unmodified goal text for the Gemini CU loop., Core iter_turns body — see :meth:`iter_turns` for the public contract. Safety… (+33 more)

### Community 7 - "Docker Xdo"
Cohesion: 0.06
Nodes (47): _blocked_cmd_match(), _effective_allowed_commands(), _open_terminal(), Move the mouse to (x,y) without clicking., Middle-click at (x,y) via xdotool., Copy the current selection to clipboard via xdotool., Gracefully close a window via EWMH using wmctrl -c., Capture the full screen via scrot. (+39 more)

### Community 8 - "Tests Effort"
Cohesion: 0.07
Nodes (19): OpenAICUClient, OpenAI Responses API computer-use client. Uses the built-in ``computer`` tool…, Compose provider instructions without changing the advertised tools., GPT-5.5 defaults to ``medium`` and accepts ``xhigh``., Regression guards for the April 2026 OpenAI reasoning-effort enum. GPT-5.5…, TestOpenAIReasoningEffort, asyncio, Preserve successful long-running workflows: many turns whose actions vary… (+11 more)

### Community 9 - "Frontend Src Api"
Cohesion: 0.08
Nodes (31): api, ApiError, getAppToken(), request(), setAppToken(), AnalyticsPage(), App(), AuditPage() (+23 more)

### Community 10 - "Tests Engine Scale"
Cohesion: 0.07
Nodes (22): get_claude_scale_factor(), Return True when the Claude run should use the 2025-11-24 CU path. Prefer the…, Compute Anthropic screenshot scale factor per official docs. Returns a factor…, Resize a PNG screenshot by *scale* factor. Returns (resized_png_bytes,…, resize_screenshot_for_claude(), _uses_claude_20251124(), ``computer_20251124`` models are 1:1 at typical resolutions., Scaling factor computation per Anthropic docs. (+14 more)

### Community 11 - "Backend V2 Session"
Cohesion: 0.18
Nodes (41): analytics(), compile_workflow(), create_credential_session(), create_session(), create_workflow(), create_workflow_version(), decide_safety(), delete_credential_session() (+33 more)

### Community 12 - "Backend Engine Search"
Cohesion: 0.07
Nodes (34): _anthropic_web_search_cache_key(), _anthropic_web_search_error_message(), _anthropic_web_search_probe_lock(), _claude_caching_on(), ClaudeCUClient, _extract_claude_sources(), _is_anthropic_web_search_enablement_error(), Any (+26 more)

### Community 13 - "Backend Infra Trace"
Cohesion: 0.09
Nodes (38): assert_invariants(), _cli(), _default_trace_dir(), _digest(), drop_trace(), finalize_session(), flush(), _get_or_create() (+30 more)

### Community 14 - "Backend Models Scroll"
Cohesion: 0.07
Nodes (41): keyboard, mouse, navigation, scroll, special, wait, allowed_actions, categories (+33 more)

### Community 15 - "Backend Server Events"
Cohesion: 0.10
Nodes (34): BaseModel, _RateLimiter, Body for the safety-confirm endpoint., Body for the key validation endpoint., Serve the built SPA while preserving real 404s for service routes., Simple sliding-window rate limiter keyed by caller identity (e.g. IP)., Configure the limiter with *max_calls* per *window_seconds* per key., SafetyConfirmRequest (+26 more)

### Community 16 - "Tests Agent"
Cohesion: 0.06
Nodes (25): _parse_cors_origins(), agent_service(), _load_agent_service_module(), fixture, Targeted coverage tests for previously-untested code paths. Covers the…, Stand-in for anthropic response text content blocks., Load docker/agent_service.py as a standalone module (it's not on sys.path)., Import docker/agent_service.py once for the test module. (+17 more)

### Community 17 - "Tests Engine Sonnet46"
Cohesion: 0.06
Nodes (23): _capture_create_kwargs(), client(), executor(), _FakeMessageStream, _minimal_png(), _png_bytes(), fixture, Sonnet 4.6 must send ``computer_20251124`` + ``computer-use-2025-11-24``, never… (+15 more)

### Community 18 - "Tests Engine Opus47"
Cohesion: 0.09
Nodes (13): asyncio, When scaling is active, coordinates should be upscaled., Claude adapter's zoom dispatch must call the executor with the validated region…, An inverted region must fail-fast without calling the executor — no crash, no…, Drive one turn through ``iter_turns`` and capture the…, Test _execute_claude_action for all supported Claude actions., Opus 4.7 with CUA_OPUS47_HIRES=1 at 2560x1600 keeps 1:1 coordinates — default…, Long edge > 2576 still clamps — the flag only drops the pixel-count cap, not… (+5 more)

### Community 19 - "Backend Engine Openai"
Cohesion: 0.08
Nodes (28): _extract_openai_output_text(), Collect assistant text blocks from a Responses API output list., _extract_openai_sources(), _openai_action_is_progress(), _openai_final_asks_for_task(), _openai_final_is_blocker(), _openai_final_is_generic(), _openai_final_needs_more_computer_use() (+20 more)

### Community 20 - "Backend V2 Fallback"
Cohesion: 0.24
Nodes (25): ContractModel, CredentialSessionInput, ErrorEnvelope, FallbackRouteInput, GoogleOAuthStartInput, BaseModel, SafetyDecisionInput, SessionInput (+17 more)

### Community 21 - "Backend Infra Key"
Cohesion: 0.08
Nodes (21): _clamp_float(), _clamp_int(), _detect_key_source(), _env_bool(), get_all_key_statuses(), KeyStatus, _mask_key(), Application configuration with environment-based settings. (+13 more)

### Community 22 - "Project Codebase"
Cohesion: 0.08
Nodes (31): Changelog, Contributor Covenant 3.0, Contributing, Business Evaluation Guide, Controlled Pilot, Codebase Architecture, Codebase Concerns, Change Discipline (+23 more)

### Community 23 - "Tests Only"
Cohesion: 0.09
Nodes (14): Run-loop must carry ``reasoning.encrypted_content`` and must never attempt…, TestOpenAIZDRReplay, _FakeExecutor, asyncio, A single computer_call with three actions should: * Dispatch all three actions…, If a computer_call has `.action` but no `.actions`, that single action should…, OpenAI docs allow a screenshot-first turn, but that is only observation. A…, If the model ignores the nudge and still returns only a generic final answer,… (+6 more)

### Community 24 - "Backend Infra Container"
Cohesion: 0.09
Nodes (29): build_image(), _ensure_agent_token(), get_container_status(), get_state(), is_container_running(), Docker container lifecycle management., Run a command as an explicit argument list (no shell interpretation)., Build the CUA Docker image from docker/Dockerfile. (+21 more)

### Community 25 - "Tests Token"
Cohesion: 0.08
Nodes (14): _FakeHeaders, _make_handler(), parametrize, Construct an AgentHandler without running the BaseHTTPRequestHandler init., _authorized must honour the X-Agent-Token header and exempt /health., Sanity: token comparison is via hmac.compare_digest (no plain ==)., /vnc/{path} should reject traversal + non-whitelisted paths and surface…, /api/v1/* must route to the same handlers as /api/*. (+6 more)

### Community 26 - "Tests Session"
Cohesion: 0.13
Nodes (22): JsonFormatter, Inject the current ``session_id`` ContextVar into every LogRecord., Context manager that binds *session_id* for the duration of the block., Single-line JSON formatter with session-id correlation. Emits: timestamp…, session_context(), SessionIdFilter, StringIO, fixture (+14 more)

### Community 27 - "Backend V2 Session"
Cohesion: 0.12
Nodes (6): Checkpoint, _now(), Any, Path, SQLite WAL persistence for v2 sessions, audit records, metrics, and workflows., SqliteStore

### Community 28 - "Docker Action"
Cohesion: 0.09
Nodes (21): BaseHTTPRequestHandler, AgentHandler, _cached_action_result(), _do_wait(), _is_action_enabled(), Sleep for *duration* seconds (clamped to 0.1–10s)., HTTP handler for the supported desktop automation mode., Redirect HTTP request logging to the module logger. (+13 more)

### Community 29 - "Frontend Dom"
Cohesion: 0.07
Nodes (28): compilerOptions, allowJs, allowSyntheticDefaultImports, esModuleInterop, exactOptionalPropertyTypes, forceConsistentCasingInFileNames, isolatedModules, jsx (+20 more)

### Community 30 - "Backend Act"
Cohesion: 0.15
Nodes (4): DesktopExecutor, Translate CU actions into agent_service ``/action`` calls., Convert raw coordinates to pixel values, denormalizing if needed., POST an action to the agent_service and return the JSON result.

### Community 31 - "Project Port"
Cohesion: 0.17
Nodes (26): _bootstrap(), _clear_dev_ports(), _clear_port(), _compose_down(), _compose_restart(), _dotenv_values(), _env_int(), _error() (+18 more)

### Community 32 - "Tests Engine Tool"
Cohesion: 0.15
Nodes (22): _canonical(), CanonicalAction, CanonicalActionType, parse_anthropic_action(), parse_gemini_action(), parse_openai_action(), ProtocolActionError, Any (+14 more)

### Community 33 - "Backend V2 Parse"
Cohesion: 0.11
Nodes (20): Provision the vector store and upload all attached files. Implements the…, Delete the per-session vector store at run-loop exit. Best-effort: a transient…, cleanup_openai_vector_store(), close_store(), delete_file(), prepare_anthropic_documents(), prepare_openai_file_search(), Any (+12 more)

### Community 34 - "Backend Store"
Cohesion: 0.13
Nodes (10): FakeExecutor, Minimal executor stub used to verify Claude action translation., _png_bytes(), parametrize, ``computer_20251124`` rejects ``budget_tokens``; must send adaptive., Safety decision ``require_confirmation`` routes through ``on_safety``., Verify the OpenAI runtime loop sends the expected follow-up payloads., TestClaudeThinkingMode (+2 more)

### Community 35 - "Backend Infra Bytes"
Cohesion: 0.13
Nodes (16): Persist an uploaded file by reading from an async stream in chunks., upload_file_stream(), extract_text(), _mime_for(), Path, Persist *data* as a new uploaded file. Raises: ValueError: extension/size/magic…, Persist an uploaded file by reading it in chunks. This avoids materializing the…, Return the metadata for *file_id* or ``None`` if unknown. (+8 more)

### Community 36 - "Docker Window"
Cohesion: 0.10
Nodes (21): _env_bool(), _expand_app_launch_candidates(), _is_safe_upload_path(), main(), Internal agent service — runs INSIDE the Docker container. Provides a…, Launch an application by command name. After a successful launch the new window…, Start the HTTP agent service for desktop automation., Return True if *target* resolves inside an allowed upload prefix. Prefix-only… (+13 more)

### Community 37 - "Tests Engine Only"
Cohesion: 0.14
Nodes (6): skip, OpenAI CU requests stay computer-only when web planning is enabled., Gemini CU requests stay computer-only when web planning is enabled., TestGeminiGoogleSearch, TestOpenAIWebSearch, TestSearchEnabledRequiresComputerAction

### Community 38 - "Backend Engine Loop"
Cohesion: 0.12
Nodes (12): Legacy callback-driven driver — now a thin wrapper over ``iter_turns``.…, Map Claude computer tool actions to executor calls. Claude actions…, Handle double_click, right_click, triple_click, and middle_click., TurnEvent, Yield Gemini turn events for per-turn consumers. Wraps :meth:`_iter_turns_core`…, Drive the native iterator while preserving the legacy callback API., CUTurnRecord, Record of one agent-loop turn, emitted via on_turn callback. (+4 more)

### Community 39 - "Tests Upload"
Cohesion: 0.15
Nodes (15): Validate provider/file-id compatibility and return deduped ids., validate_attached_files(), FileStore, Process-wide in-memory registry of uploads, persisted to disk., Remove an upload from disk + registry. Returns True on success., Sweep uploads older than ``UPLOAD_TTL_SECONDS``. Returns count removed., Wipe the entire upload root. Wired into FastAPI shutdown., _ChunkedUpload (+7 more)

### Community 40 - "Tests Engine Gemini"
Cohesion: 0.13
Nodes (14): Config, Full HTTP URL for the in-container agent service., Runtime configuration — values come from env vars or runtime overrides., _FakeContent, _FakePart, The CU guide recommends 1440x900 for best coordinate accuracy. The project's…, Gemini wants a Chromium-class browser even though Noble's apt packages are…, Regression guard: Anthropic's reference uses Firefox-ESR. This commit must not… (+6 more)

### Community 41 - "Tests Engine Openai"
Cohesion: 0.10
Nodes (12): OpenAI CU guide's browser hardening contract: when the agent spawns a Chromium-…, Regression guard: ``--disable-file-system`` protects the sandbox if the…, The browser subprocess must NOT inherit the full host env (it would leak…, Source-scan regression guard: no ``env={**os.environ, ...}`` on the browser…, OpenAI CU guide Option 1 mandates XFCE4 as the WM., Light-locker / xfce4-screensaver / xfce4-power-manager steal focus from xdotool…, Regression guard: S1's shared viewport survives. OpenAI's reference Dockerfile…, ``_build_openai_computer_call_output`` is the single code path that packs… (+4 more)

### Community 42 - "Tests Bytes"
Cohesion: 0.14
Nodes (11): _FakeExecutor, _minimal_png(), asyncio, Anything shorter than 100 B is treated as a capture failure., A valid PNG must still proceed to ``messages.create``., Current-tool Claude models use adaptive thinking; legacy models keep the fixed-…, Return bytes large enough to pass the >=100 B guard., Fake executor that returns a caller-controlled screenshot once. (+3 more)

### Community 43 - "Tests Openai"
Cohesion: 0.13
Nodes (10): _build_openai_computer_call_output(), Build the Responses API follow-up item for a computer call., Strip output-only fields before replaying Responses items statelessly., _sanitize_openai_response_item_for_replay(), Verify OpenAI computer-call follow-up payloads., TestOpenAIHelpers, Spec-named guard (April 2026 followup): every ``computer_call_output`` emitted…, Spec-named guard (April 2026 followup): when an assistant message carries… (+2 more)

### Community 44 - "Backend Models Engine"
Cohesion: 0.13
Nodes (11): EngineCertifier, Path, Engine and tool validation framework. Loads ``engine_capabilities.json`` and…, Verify top-level schema structure and required fields per engine., Verify all engines are properly registered., Return list of missing binary names for the given engine., Verify categories <-> allowed_actions parity for one engine., Run a safe, non-destructive execution probe for *engine_name*. Returns one of:… (+3 more)

### Community 45 - "Docker Window"
Cohesion: 0.11
Nodes (20): _browser_minimal_env(), _dismiss_known_modals(), _open_url_in_browser(), _post_launch_normalize(), Open URL in a deterministic browser — avoids xdg-open first-run problems., Focus a window by name or class., Find the most-recently-created window, activate it, and normalise. *hint* is…, Return xdotool window IDs matching *identifier* by name. (+12 more)

### Community 46 - "Scripts Shutdown"
Cohesion: 0.18
Nodes (14): build_failure_message(), _extract_announcement_block(), fetch_changelog_html(), find_shutdown_announcement(), _find_shutdown_in_section(), html_to_lines(), _is_date_heading(), _is_model_code_line() (+6 more)

### Community 47 - "Tests Key"
Cohesion: 0.13
Nodes (7): Redact API-key-shaped tokens from free-form text., scrub_secrets(), Gemini API-key calls use the native async Interactions client., TestGeminiNativeAsync, TestOriginGating, TestSecretScrubbing, TestTokenEnvFile

### Community 48 - "Docs Zero"
Cohesion: 0.14
Nodes (18): Integrations, Technology Stack, Repository Structure, Testing, Deployment Guide, Three Direct Provider Routes, Gemini Successor Evaluation Checklist, CUAF Preview Frames (+10 more)

### Community 49 - "Tests Engine Prompt"
Cohesion: 0.15
Nodes (11): get_system_prompt(), Any, Return the system prompt for the computer_use engine. Parameters ----------…, Negative control: non-4.7 Claude models still benefit from the scaffolding and…, Opus 4.6 (computer_20251124 tool but older reasoning) still benefits from…, Callers that don't pass a model id must get the conservative scaffolded prompt., TestPromptAudit, parametrize (+3 more)

### Community 50 - "Backend V2 Credential"
Cohesion: 0.16
Nodes (8): CredentialSession, CredentialVault, ProviderCredential, Any, BaseModel, Process-local credential sessions; secrets are never persisted or serialized., test_credential_vault_accepts_process_local_google_oauth(), test_credential_vault_never_serializes_secrets_and_expires()

### Community 51 - "Backend Engine Execute"
Cohesion: 0.17
Nodes (7): _invoke_safety(), Any, Invoke a safety callback that may be sync or async. Returns False if None., Build the action executor for this session. Unified Computer Use surface: a…, Execute a CU task end-to-end using the native tool protocol. Args: goal:…, Dispatch to the provider's ``iter_turns`` contract. Claude / Gemini: use their…, _SafetyPolicyExecutor

### Community 52 - "Backend Back"
Cohesion: 0.14
Nodes (13): configure_logging(), install(), Logger, Attach the :class:`SessionIdFilter` to every handler on *root_logger*.…, Install the session-id filter and (optionally) a JSON formatter. Call once at…, _enforce_public_bind_guardrail(), main(), Entry point for the backend server. (+5 more)

### Community 53 - "Tests Response"
Cohesion: 0.14
Nodes (4): C10: 5xx / error-payload responses should fall back to docker exec, but 401/403…, Build a Response that has its ``.request`` set so ``raise_for_status`` works…, TestScreenshotFallback, _Response

### Community 54 - "Tests Rejected"
Cohesion: 0.12
Nodes (5): Test input validation on POST /api/agent/start., Legacy ``mode="browser"`` is accepted for wire compatibility but is now ignored…, Any value supplied for the legacy ``mode`` field is accepted and ignored under…, Without any API key source, should get a clear error., TestAgentStartValidation

### Community 55 - "Backend Engine Model"
Cohesion: 0.16
Nodes (10): default_openai_reasoning_effort_for_model(), get_model_capabilities(), _lookup_claude_cu_config(), Return the allowed_models.json entry for *model_id* as a plain dict. A2: single…, Look up cu_tool_version / cu_betas from allowed_models.json. Returns…, Return the OpenAI reasoning default for a model slug, from registry metadata.…, _ensure_openai_ga_model_is_in_registry(), Reject GPT-5.5-family GA slugs that are absent from the registry. (+2 more)

### Community 56 - "Backend Task"
Cohesion: 0.14
Nodes (9): AgentLoop, Any, Runs the perceive → think → act loop for a CUA session., Initialise a new agent loop for *task* using the given provider/model., Create a :class:`StructuredError`, append it to the error log, and return it., Invoke a callback, swallowing exceptions to keep the loop alive., The 60 s wait_for path must return False (deny) on TimeoutError. Captures the…, TestSafetyTimeoutAutoDeny (+1 more)

### Community 57 - "Backend Models Engine"
Cohesion: 0.15
Nodes (10): EngineSchema, get_default_schema_path(), Any, Path, Return the full ``EngineSchema`` for *engine_name*, or ``None``., Resolve an action string to its canonical ActionType value. Returns the…, Return the package-relative capability schema path., Typed representation of a single engine's capability block. (+2 more)

### Community 58 - "Frontend Testing"
Cohesion: 0.13
Nodes (15): eslint, devDependencies, eslint, @testing-library/dom, @testing-library/react, @testing-library/user-event, typescript, @vitejs/plugin-react (+7 more)

### Community 59 - "Scripts Build"
Cohesion: 0.30
Nodes (14): build_site(), content_fingerprint(), convert_source(), find_pandoc(), main(), Path, Build the self-contained Zero to Hero handbook website., render_articles() (+6 more)

### Community 60 - "Tests Docker Agent"
Cohesion: 0.18
Nodes (12): agent_service(), agent_service_legacy(), dockerfile(), entrypoint(), _load_agent_service(), fixture, Import docker/agent_service.py as a standalone module. Mirrors the loader in…, Load the module with legacy actions enabled. ``run_command`` is intentionally… (+4 more)

### Community 61 - "Tests Token"
Cohesion: 0.21
Nodes (6): When ``CUA_WS_TOKEN`` is set, /vnc/websockify must reject upgrades without a…, Build a MagicMock WebSocket with the given ?token=... and Origin.…, Success criterion (1): CUA_WS_TOKEN set + no token → close(4401) BEFORE accept…, Success criterion (2): CUA_WS_TOKEN set + matching ?token= → proxy proceeds…, Success criterion (3): CUA_WS_TOKEN unset → no token required on either…, TestVncWebsockifyTokenGating

### Community 62 - "Tests Engine Screenshot"
Cohesion: 0.22
Nodes (9): _prune_claude_context(), Replace base64 screenshot data in old turns with a placeholder. Keeps the first…, Verify _prune_claude_context replaces old screenshots with placeholders., Build a realistic Claude message list with n tool_result pairs., Messages shorter than keep_recent should not be pruned., Old tool_result images should become [screenshot omitted]., The most recent keep_recent messages should retain screenshots., The first user message always keeps its screenshot. (+1 more)

### Community 63 - "Backend V2 Execution"
Cohesion: 0.18
Nodes (5): Any, V2Orchestrator, ExecutionStarter, Queue, Task

### Community 64 - "Docker Key"
Cohesion: 0.15
Nodes (14): _map_key_combo_xdotool(), Press a multi-key combo via xdotool., Map a user key string to an xdotool key combo., Click at (x,y) via xdotool., Composite "type at coordinate": click → optionally clear → type → optionally…, Hold a key down via xdotool., Release a held key via xdotool., Press and release a key combo via xdotool. (+6 more)

### Community 65 - "Tests Ready"
Cohesion: 0.18
Nodes (7): TestClient, client(), fixture, parametrize, MonkeyPatch, Even when the container is stopped / unknown, liveness stays up. This is the…, Both the docker probe AND the key check fail → both reasons surface. Operators…

### Community 66 - "Tests Docker Command"
Cohesion: 0.20
Nodes (8): Drive ``_dispatch_desktop`` through the ``run_command`` branch with a stub…, Invoke the ``run_command`` branch with minimal plumbing., Baseline: the allowlist still denies ``curl`` (not in allow-set). Establishes…, Executable IS on the allowlist (``bash`` isn't, use ``python3``), but the argv…, Trivial casing tricks must not bypass the gate., Gate-type leakage check: the error payload for a blocked pattern on an…, Regression guard for criterion #2: a request that passes both gates must still…, TestRunCommandEnforcement

### Community 67 - "Tests Engine Loop"
Cohesion: 0.19
Nodes (4): asyncio, fixture, Claude CU requests stay computer-only when web planning is enabled., TestClaudeWebSearch

### Community 68 - "Backend Screenshot"
Cohesion: 0.19
Nodes (11): _agent_headers(), capture_screenshot(), check_service_health(), _fallback_docker_screenshot(), Enum, Desktop Computer Use executor. This module owns screenshot capture plus…, Grab a screenshot with docker exec + scrot when the service is unhealthy., Return True when the desktop agent-service health endpoint is reachable. (+3 more)

### Community 69 - "Backend V2 Retention"
Cohesion: 0.22
Nodes (3): FrameRetentionStore, Path, Bounded audit-frame retention with age and byte-budget eviction.

### Community 71 - "Tests Engine Gemini"
Cohesion: 0.27
Nodes (7): denormalize_x(), denormalize_y(), Convert Gemini normalized x (0-999) to a pixel coordinate., Convert Gemini normalized y (0-999) to a pixel coordinate., Gemini normalised coords → pixel conversion., TestDenormalize, 0-999 normalized → pixels. The scaling helpers in ``backend.engine`` are the…

### Community 72 - "Tests Docker Validate"
Cohesion: 0.24
Nodes (5): Reject names containing shell metacharacters., _validate_name(), Verify docker_manager input validation and security flags., Verify the source code includes --security-opt and resource limits., TestDockerManagerSecurity

### Community 73 - "Backend Models Engine"
Cohesion: 0.17
Nodes (7): EngineCapabilities, Schema version string., All registered engine names., Return the set of allowed actions for *engine_name*. Returns an empty frozenset…, Return ``True`` if *action* is valid for *engine_name*. This is the primary…, Validate and return a ``(ok, message)`` tuple. On failure the message explains…, Machine-readable engine capability registry. Parameters: schema_path: Path to…

### Community 74 - "Backend Event"
Cohesion: 0.18
Nodes (11): arm(), clear(), get_or_create_event(), Return an existing event for the session or create a fresh one., Arm a fresh safety prompt for *session_id*; return ``(nonce, event)``. Creates…, Constant-time check that *supplied* matches the session's armed nonce., Signal an already-armed event. Returns False if the session isn't armed. Unlike…, Drop any pending event, decision, and nonce for the session. (+3 more)

### Community 75 - "Backend V2 Models"
Cohesion: 0.20
Nodes (7): _CatalogDocument, ModelCatalog, ModelRoute, BaseModel, Path, Validated, transport-aware Computer Use model catalog., test_model_catalog_is_transport_aware_and_computer_use_only()

### Community 76 - "Tests Docker Match"
Cohesion: 0.17
Nodes (6): Pure-function tests: _blocked_cmd_match must look at the whole argv, not just…, argv[0] alone is benign; the dangerous phrase hides in a later arg. The…, Pattern-matching must survive trivial ``SHUTDOWN`` / ``Rm -Rf /`` casing tricks., ``:(){`` is the classic fork-bomb prefix., Negative control: an ordinary ``ls /tmp`` must not trip., TestBlockedCmdMatch

### Community 77 - "Backend Scrub"
Cohesion: 0.25
Nodes (7): _scrub_secrets(), Request the loop to stop after the current step., Create a LogEntry and forward it to the log callback., Execute the provider-native Computer Use loop., Delegate the entire task to the native CU protocol engine. The CU engine runs…, Scrub secret-shaped tokens from a Gemini grounding dict before broadcast.…, _scrub_grounding()

### Community 78 - "Backend Models Check"
Cohesion: 0.22
Nodes (7): EngineReport, Any, Certification result for a single engine., Serialise to a JSON-friendly dict., Check binary and env-var deps for one engine., Populate *report* with any missing binary dependencies from *reqs*., Populate *report* with any missing environment variables from *reqs*.

### Community 79 - "Tests Docker Action"
Cohesion: 0.20
Nodes (4): _make_post_handler(), Construct a minimal POST /action handler and capture its response., ``_is_action_enabled`` is the single source of truth the handler consults…, TestActionGate

### Community 80 - "Tests Clamped"
Cohesion: 0.29
Nodes (4): S3 — numeric env values must be clamped to safe ranges., Build a Config under a fully-scrubbed env so existing vars don't leak in., MAX_STEPS must respect the 200-step hard cap enforced upstream., TestEnvClamping

### Community 81 - "Tests Screenshot"
Cohesion: 0.20
Nodes (5): Grab a screenshot via ``docker exec scrot`` as last resort., RuntimeError, If the agent loop raises, the session must be marked ERROR, the finish event…, TestCleanupSessionResilience, TestRunAndNotifyErrorPath

### Community 82 - "Docker Screen"
Cohesion: 0.20
Nodes (9): DISPLAY, HEIGHT, PATH, PYTHONPATH, SCREEN_DEPTH, SCREEN_HEIGHT, SCREEN_WIDTH, entrypoint.sh script (+1 more)

### Community 83 - "Tests Container"
Cohesion: 0.20
Nodes (6): The previous behaviour returned success from ``_wait_for_service`` whenever the…, Container process survives but /health always errors → False, and get_state()…, Happy path: a 200 from /health flips state to ready and clears any prior…, End-to-end: when the cached readiness says the sandbox isn't ready, POST…, If ``start_container()`` fails because an already-running container never…, TestContainerReadinessGating

### Community 84 - "Backend Client"
Cohesion: 0.22
Nodes (6): _get_client(), AsyncClient, Return a reusable agent-service HTTP client for lightweight probes., Return the per-service-URL shared httpx client., Return the shared-secret header for authenticated agent_service calls., Capture a screenshot via the agent_service, with docker exec fallback.

### Community 85 - "Frontend React"
Cohesion: 0.22
Nodes (9): dependencies, lucide-react, react, react-dom, react-router-dom, lucide-react, react, react-dom (+1 more)

### Community 86 - "Frontend Build"
Cohesion: 0.22
Nodes (9): scripts, build, coverage, dev, lint, preview, test, test:run (+1 more)

### Community 87 - "Tests Key"
Cohesion: 0.22
Nodes (4): C8: model-emitted key tokens are restricted to an explicit allowlist., C8 follow-up: ``hold_key`` must enforce the same allowlist as…, Preserve normal supported behavior: ``Shift`` held for a second is the…, TestKeyAllowlist

### Community 88 - "Tests Executor"
Cohesion: 0.25
Nodes (6): Q4: parity guard — every literal action name the Claude/OpenAI adapters pass to…, Helper: execute a click with include_screenshot and capture wire payloads., P1: execute(..., include_screenshot=True via args) adds the flag to the /action…, _run_bundled_capture(), test_every_client_emitted_action_has_executor_handler(), test_p1_include_screenshot_is_sent_and_bundled_frame_surfaces()

### Community 89 - "Tests Nonce"
Cohesion: 0.33
Nodes (4): client(), _FakeLoop, fixture, TestSafetyConfirmAuthz

### Community 90 - "Tests Rejected"
Cohesion: 0.22
Nodes (4): The previous implementation used ``str.startswith(root + os.sep)`` which is…, ``/tmpX/...`` must NOT be accepted just because ``/tmp`` is allowed., Behaviour preserved from the prefix-string version: the root directory itself…, TestUploadPathContainment

### Community 91 - "Tests Handbook"
Cohesion: 0.25
Nodes (3): HandbookParser, HTMLParser, test_generated_handbook_is_offline_and_internally_linked()

### Community 92 - "Tests Engine Turn"
Cohesion: 0.50
Nodes (3): _prune_gemini_context(), Drop old Gemini history turns atomically while keeping kept turns intact.…, TestGeminiHistoryPruning

### Community 93 - "Backend Models Validation"
Cohesion: 0.36
Nodes (6): CertificationReport, main(), _print_table(), Aggregate certification result for all engines., Serialise to a JSON-friendly dict., CLI entry point for ``python -m backend.models.validation``.

### Community 94 - "Backend Prompt"
Cohesion: 0.29
Nodes (7): Validate that: 1. All engine actions in engine_capabilities.json exist in…, validate_tool_parity(), _extract_prompt_actions(), System prompt for the Computer Use engine. Provides a single…, Extract action keywords from a system prompt string., Cross-check actions mentioned in the CU prompt against the capability schema.…, validate_prompt_actions()

### Community 95 - "Tests Event"
Cohesion: 0.36
Nodes (4): Any, Validate a dict payload against the registered event schema. Returns ``None``…, validate_outbound(), TestWsSchemaValidation

### Community 97 - "Tests Docker"
Cohesion: 0.25
Nodes (3): When _wait_for_service returns False we must docker rm the half-started…, Unit tests for the fresh-run + teardown branches without touching docker., TestStartContainer

### Community 98 - "Scripts Build"
Cohesion: 0.43
Nodes (6): main(), Build a static, multipage htmx site from every tracked .md file in the repo.…, Point in-doc links to other tracked .md files at their generated page., rewrite_md_links(), slug_for(), title_for()

### Community 100 - "Tests Server"
Cohesion: 0.29
Nodes (7): clear_server_rate_limiters(), client(), fixture, Create a FastAPI TestClient for backend.server.app., Import backend.server with the publisher's IO stubbed out. We patch: *…, Keep per-process rate limiters from leaking state across tests., server_mod()

### Community 101 - "Assets Agent"
Cohesion: 0.33
Nodes (6): Agent Action Timeline, CUA Computer Using Agent, GPT-5.5 Model, CUA Computer Using Agent Interface, Manual API Key Source, Agent Step Limit

### Community 102 - "Evals Agent"
Cohesion: 0.40
Nodes (4): ``POST /api/agent/start`` must refuse when the sandbox is unready., Sanity check: same payload, ``agent=ready`` → not a 409., _start_payload(), TestDegradedContainerStartup

### Community 103 - "Scripts Build"
Cohesion: 0.53
Nodes (5): copy(), main(), Path, Build reproducible v2 release assets without publishing them., run()

### Community 104 - "Project Setup"
Cohesion: 0.60
Nodes (5): error(), info(), setup.sh script, usage(), warn()

### Community 106 - "Tests Should"
Cohesion: 0.33
Nodes (3): __init__.py should be well below the pre-split 1992 lines (Q2)., Public names still importable from the package root., TestEnginePackageSplit

### Community 107 - "Backend Action"
Cohesion: 0.40
Nodes (4): Any, Return the post-action settle delay appropriate for action *name*., Map a CU action to the agent_service ``/action`` endpoint., _settle_delay_for()

### Community 109 - "Backend Models Allowed"
Cohesion: 0.40
Nodes (4): description, models, $schema, version

### Community 110 - "Backend Models Computer"
Cohesion: 0.40
Nodes (4): models, $schema, verified_at, version

### Community 111 - "Frontend Package"
Cohesion: 0.40
Nodes (4): name, private, type, version

### Community 112 - "Tests Docker Version"
Cohesion: 0.40
Nodes (3): parametrize, Version / revision / created must be ARG-backed so a release pipeline can stamp…, TestOciLabels

### Community 113 - "Tests Docker Exec"
Cohesion: 0.40
Nodes (3): ``exec python ...`` as the last meaningful line means the Python process…, ``docker/agent_service.py`` must register SIGTERM/SIGINT handlers so ``docker…, TestSignalCleanShutdown

### Community 115 - "Tests Engine Single"
Cohesion: 0.40
Nodes (3): No per-model viewport fork: the single 1440x900 default from S1 covers Sonnet…, The Anthropic computer-use-demo reference package set from S1 is the single…, TestSonnet46SharesSandbox

### Community 120 - "Docker Logrecord"
Cohesion: 0.50
Nodes (3): _ActionIdFilter, LogRecord, Inject the current request's action_id onto every LogRecord.

### Community 121 - "Docker Terminal"
Cohesion: 0.50
Nodes (4): _is_terminal_focused(), Check if the currently focused window is a terminal emulator. Terminals…, Copy text to clipboard then paste via Ctrl+V (or Ctrl+Shift+V in terminals)., _xdo_paste()

### Community 122 - "Docker Type"
Cohesion: 0.50
Nodes (4): Type text via xdotool with modifier-key safety. Strategy: 1. Try ``xdotool…, Send *text* one character at a time via ``xdotool key``. This bypasses the…, _xdo_type(), _xdo_type_key_per_char()

### Community 123 - "Evals Guide"
Cohesion: 0.50
Nodes (4): Computer Use Prompt Guide, Offline Deterministic Evals, Operator Usage Guide, Technical Architecture Documentation

### Community 124 - "Evals Active"
Cohesion: 0.50
Nodes (4): _clear_active_registries(), client(), fixture, Reset the active-session registries before each eval run.

### Community 131 - "Tests Engine Reference"
Cohesion: 0.50
Nodes (3): Verify pruning constant matches reference implementations., Should match Google reference MAX_RECENT_TURN_WITH_SCREENSHOTS and Anthropic…, TestContextPruneConstant

### Community 132 - "Backend Engine Collect"
Cohesion: 0.67
Nodes (3): _collect_transient_error_types(), Return a tuple of exception classes worth retrying., BaseException

### Community 133 - "Backend Infra Bind"
Cohesion: 0.67
Nodes (3): bind_session_id(), Bind *session_id* into the current async context. Returns the…, Token

### Community 134 - "Docker Compose"
Cohesion: 0.67
Nodes (3): Docker Compose Configuration, Docker Security Notes, Container Hardening Posture

## Knowledge Gaps
- **131 isolated node(s):** `$schema`, `version`, `description`, `models`, `$schema` (+126 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **42 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_xdo()` connect `Docker Xdo` to `Docker Key`, `Docker Window`, `Docker Window`, `Tests Screenshot`, `Docker Terminal`, `Docker Type`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Why does `ClaudeCUClient` connect `Backend Engine Search` to `Backend Providers Run`, `Backend Store`, `Tests Engine Loop`, `Backend Engine Gemini`, `Backend Engine Loop`, `Tests Engine Default`, `Tests Engine Scale`, `Tests Bytes`, `Tests Agent`, `Tests Engine Sonnet46`, `Tests Engine Opus47`, `Backend Engine Execute`?**
  _High betweenness centrality (0.053) - this node is a cross-community bridge._
- **Why does `OpenAICUClient` connect `Tests Effort` to `Tests Session`, `Backend Providers Run`, `Backend Engine Gemini`, `Tests Engine Default`, `Tests Agent`, `Backend Engine Openai`, `Tests Only`, `Backend V2 Parse`, `Backend Store`, `Backend Engine Loop`, `Tests Key`, `Tests Engine Prompt`, `Backend Engine Execute`, `Tests Response`, `Backend Engine Model`, `Tests Token`, `Tests Container`, `Tests Key`, `Tests Bind`, `Tests Origin`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Are the 114 inferred relationships involving `patch` (e.g. with `.test_ready_agent_does_not_409()` and `.test_unready_agent_returns_409_and_no_session_row()`) actually correct?**
  _`patch` has 114 INFERRED edges - model-reasoned connections that need verification._
- **Are the 30 inferred relationships involving `OpenAICUClient` (e.g. with `ComputerUseEngine` and `CUTurnRecord`) actually correct?**
  _`OpenAICUClient` has 30 INFERRED edges - model-reasoned connections that need verification._
- **Are the 50 inferred relationships involving `AgentLoop` (e.g. with `ActionType` and `AgentAction`) actually correct?**
  _`AgentLoop` has 50 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `ClaudeCUClient` (e.g. with `ActionExecutor` and `CUActionResult`) actually correct?**
  _`ClaudeCUClient` has 12 INFERRED edges - model-reasoned connections that need verification._