# AGENTS.md

Repository conventions for any coding agent (Antigravity, Claude Code, Gemini CLI, Codex) working in `AI-Devcamp-rowing-coach-agent`. Read this before making changes.

## 1. What This Project Is

`AI-Devcamp-rowing-coach-agent` is an enterprise-grade Google ADK agent serving as the personal AI coach for Masters rowing ("Coach David Montgamery" at Aviron Club du Lac - ACL). Built across the four Gemini Enterprise Agent Platform pillars (Build, Scale, Govern, Optimise), it synthesizes:
- **Evidence-based exercise physiology** and physics (Dr. Valery Kleshnev's rowing mechanics, Seiler 3-zone / 5-zone lactate distribution, and the authoritative 13-week Phase 1 periodization curriculum).
- **Custom algorithmic tools** (W1-W13 progression, L4 lactate threshold stroke-rate alignment, non-linear erg/water pacing mathematics).
- **Agent Skills (`SkillToolset`)** loading progressive disclosure coaching rules (`masters-rowing-rules`).
- **Sub-agents (`AgentTool`)** isolating `google_search` for weather, regatta calendars (Ligue Sud PACA, FFAviron), and rigging geometry.
- **Intervals.icu Integration (`McpToolset`)** connecting athlete wellness and workout export with mandatory human-in-the-loop approval gating (`require_confirmation=True`).
- **Enterprise Governance (Lab 3)**: Pure-ASGI boundary PII redaction (`pii_middleware.py`), Google Cloud Model Armor regional prompt/response screening (`guardrails.py`), and Agent Registry discovery (`governance.py`).
- **Architectural Blueprint v0.2**: Decoupled sovereign EU boundary (`europe-west1`), multi-region inference router on HTTP 429 (`llm_router.py`), reversible bracketed PII vault (`pii_vault.py`), and dual-backend zero-cost persistence (Firestore free tier / SQLite).
- **Evaluation & Quality Flywheel (Lab 4)**: Domain-critical golden evals (`evals/golden.json`), LLM-as-judge rubrics (`evals/eval_config.json`), and fail-closed CI release gating.

---

## 2. Directory Structure & Invariants

```
AI-Devcamp-rowing-coach-agent/
├── AGENTS.md                   # This file (project rules & conventions)
├── Part3_Govern_Logic.md       # Self-contained Lab 3 governance roadmap
├── pyproject.toml / uv.lock    # Pinned dependencies
├── main.py                     # FastAPI ASGI server (ADK Agent + Middleware)
├── local_proxy.py              # Local reverse proxy with Cloud Run IAM impersonation
├── governance.py               # Agent Registry & semantic policy CLI
├── pii_middleware.py           # Pure-ASGI Cloud DLP + Domestic phone redaction
├── rowing_coach/
│   ├── agent.py                # Root orchestrator + subagents + MCP toolsets
│   ├── tools.py                # Biomechanical & periodization physics tools
│   └── guardrails.py           # Model Armor screening & friendly refusal handler
├── backend/
│   ├── llm_router.py           # Multi-region failover router (HTTP 429)
│   ├── pii_vault.py            # Reversible bracketed athlete PII vault
│   └── db.py                   # Dual-backend persistence (Firestore / SQLite)
├── skills/
│   └── masters-rowing-rules/   # Progressive disclosure coaching skills
├── mcp/
│   └── intervals_server.py     # FastMCP Intervals.icu server
├── evals/
│   ├── golden.json             # Golden evaluation dataset
│   ├── eval_config.json        # LLM-as-judge rubrics (including tool use)
│   └── verify_eval_gate.sh     # Fail-closed CI evaluation parser
├── docs/                       # Static documentation suite
└── tests/                      # Automated pytest suite
```

---

## 3. Python Environment & Working Directory Invariant (CRITICAL)

- **Always Execute Within Project Scope:** Never run bare commands from the parent workspace root. Always `cd AI-Devcamp-rowing-coach-agent` or pass `--project AI-Devcamp-rowing-coach-agent`.
- **Strict `uv` Usage:**
  - Strictly use `uv run` for all Python executions (`uv run pytest`, `uv run python ...`).
  - NEVER run bare `python`, `python3`, `pip`, or `pytest`.
  - Add packages strictly via `uv add <package>`; never use `uv pip`.

---

## 4. Architectural Decisions & Key Learnings (Carried from Social Spark)

1. **Fail-Closed CI/CD Release Gating (Learning 30):**
   - Never rely on raw exit codes (`$?`) from `adk eval`, which exits `0` even when test cases fail (`Tests failed: 1`).
   - All evaluation scripts and CI triggers must parse test counters explicitly and abort (`exit 1`) if failures > 0 or unparseable.
2. **Mandatory Tool-Use Rubrics for Progressive Disclosure (Learning 31):**
   - Evaluating final response text alone leaves a silent failure mode where the model hallucinates an answer while completely bypassing `load_skill` or safety tools.
   - All eval suites must include `rubric_based_tool_use_quality_v1` asserting that required skills/tools were executed.
3. **Multi-Country Domestic Phone Detection (Learning 15):**
   - Cloud DLP's built-in `PHONE_NUMBER` detector misses domestic phone numbers without international prefixes.
   - Augment with custom regex detectors (UK `020...`, French `0[1-9]...`) to guarantee 100% boundary sanitization.
4. **Predictable Configuration Resolution Hierarchy (Learning 14 / POLA):**
   - Administrative CLIs (`governance.py`) must follow: Explicit Flags $\to$ Environment Variables $\to$ Local `.env` $\to$ ADC credentials. Never crash abruptly demanding manual exports.
5. **Single Human Approval Gate & Confirmation Enforcement (ADR-5):**
   - Any tool with real side-effects (scheduling workouts, sending external data) must have `require_confirmation=True`.
   - Never invoke state-modifying actions without explicit human confirmation.
6. **Token Preservation & Context Caching (Lab 4 Step 4):**
   - Preserve prompt prefixes to hit the 68% context cache ratio.
   - Enforce Mix 2 right-sizing: Flash for orchestrator, Flash-Lite for research subagent.

---

## 5. Development Cadence & Testing Mandate

- Run `uv run pytest` before and after every modification.
- Commit atomically after completing each milestone step.
- Preserve all existing comments and docstrings.

---

## 6. Hackathon & Presentation Guardrails (Always Active)
- **Pitch & Strategy Confidentiality:** NEVER commit presentation scripts, video storyboards, competition pitch notes, or implementation plans into public Git repositories or `docs/`. Save them strictly in the parent workspace directory (`/Users/duncanmoncrieff/projects/gdg-london-ai-devcamp-sept26/`) or local untracked scratchpads.
- **Branch Discipline:** During active hackathon development, always work on a dedicated feature branch (e.g., `hackathon-dev`). Never push commits to `main` or update the GitHub Pages live branch without explicit user review and approval.
- **Grounded Status Reporting:** Do not use inflated labels like "production-grade" or "enterprise-complete" for components where functionality is still partial, prototype, or actively being developed. Use precise technical descriptors (e.g. "functional prototype", "verified eval baseline").
- **Canonical Naming:** Refer to the project strictly as `Rowing Coach Agent`.
- **GCP Client Quota Isolation:** Always explicitly set `ClientOptions(quota_project_id=PROJECT_ID)` when instantiating Google Cloud SDK clients (especially Cloud DLP and Model Armor) to prevent ambient ADC project collisions.

---

## 7. Active Sprint: Hackathon Day Phase 14 (31 Oct 2026)
* **Master Tracking Document:** [`../ai-devcamp-labs/PROGRESS.md`](../ai-devcamp-labs/PROGRESS.md) (Phase 14)
* **Implementation Plan:** [`site_and_readme_redesign_plan.md`](file:///Users/duncanmoncrieff/.gemini/antigravity/brain/0814051f-3a0d-4014-9cc3-525fd6412fe3/site_and_readme_redesign_plan.md)
* **Core Sprint Tasks:**
  1. Cryptographic non-decipherable pseudonym tokens (`[ATHLETE_ath_5a77195e]`) resolving to internal biometric roster IDs in `backend/pii_vault.py` and `rowing_coach/tools.py`.
  2. Direct markdown plan return & local download in `rowing_coach/tools.py` and `local_proxy.py` (solving `/code/plans` container isolation).
  3. Interactive Approval Card UI and route in `local_proxy.py` + unblocking read-only `intervals_get_athlete_wellness`.
  4. Concept2 logbook link & erg screen telemetry ingestion tool.
  5. Documentation site redesign (`index.html`, `walkthrough.html`, `governance-and-pii.html`, `cost-economics.html`, `design-critique.html`), live GitHub Pages URL link repairs in `README.md`, authentic walkthrough screenshot embeddings, and interactive Generative UI GDPR Art. 9 simulator.
  6. Automated Documentation Site Test Suite (`tests/test_docs_site.py`) with 19 comprehensive checks covering HTML5 structure, CSS/scripts, bidirectional navigation across all 20 permutations, zero dead links/anchors, asset non-zero size, and README invariants (57/57 total repository tests green).

