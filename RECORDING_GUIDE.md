# 🎥 3-Minute Hackathon Video Demo — Step-by-Step Recording Guide

> **Event:** GDG London AI DevCamp Hackathon (Halloween Edition)  
> **Project:** `Rowing Coach Agent`  
> **Target Duration:** Exactly 3:00 minutes (180 seconds)  
> **Target URL (Primary - Recommended):**  
> `https://rowing-coach-demo-498585195540.us-central1.run.app/?code=rowing-********-devcamp-2026` (Use your active reviewer passcode)  
> **Fallback URL (Zero-latency backup):** `http://localhost:8080` (run via `./run-frontend.sh --local`)

---

## 🛠️ 1. Pre-Flight Setup Checklist (2 Minutes Before Hitting Record)

1. **Browser Window:**
   - Open Chrome at 1080p (or maximize to full screen).
   - Navigate to the Cloud Run demo URL with your active passcode: `https://rowing-coach-demo-498585195540.us-central1.run.app/?code=rowing-********-devcamp-2026`
   - Click the **"Reset Chat"** button (top right) so the workspace starts fresh and clean.
   - Ensure the browser zoom is set to **100%** (or **110%** for maximum readability).
2. **Tab Arrangement:**
   - Tab 1: Live Cloud Run Dashboard (`https://rowing-coach-demo-...`)
   - Tab 2 (Optional, quick flash): Documentation Site (`https://duncmon.github.io/AI-Devcamp-rowing-coach-agent/`)
   - Tab 3 (Optional, quick flash): GitHub Repository (`https://github.com/duncmon/AI-Devcamp-rowing-coach-agent`)
3. **Screen Recorder:**
   - Loom, OBS, QuickTime Screen Recording, or native macOS screen record (`Cmd + Shift + 5`).
   - Check microphone audio level.

---

## ⏱️ 2. The 3-Minute Step-by-Step Recording Script

### Scene 1: The Hook & Domain Problem (0:00 – 0:35) | 35 Seconds
- **What to show on screen:**
  - Start directly on the **ACL Masters Rowing Coach** dashboard on Cloud Run.
  - Point mouse cursor to the subtitle: *"Phase 1 Macrocycle (Week 8) · Coach David Persona"* and the Active Squad Profile card at bottom left.
- **What to say aloud:**
  > *"Hi everyone, I’m Duncan. High-performance masters rowers—athletes aged 30 to 70+—face a delicate physiological challenge: balancing training fatigue, cardiovascular ceilings, and mathematically exact power pacing.*
  > 
  > *Training frameworks like the Wolverine Plan Level 4 require non-linear power-to-pace conversions. Standard chatbots fail here: they hallucinate split arithmetic, lack periodization context, and leak athlete biometric health data.*
  > 
  > *Under GDPR Article 9, athlete cardiovascular data and injury notes are protected Special Category Health Data. But if you naively strip athlete names, the AI doesn't know whose training file to consult!*
  > 
  > *Meet the **Rowing Coach Agent**: an autonomous sports science co-pilot built with Google Agent Development Kit and Google Cloud Agent Runtime that solves this privacy dilemma."*

---

### Scene 2: Architectural Depth & The 4 Pillars (0:35 – 1:15) | 40 Seconds
- **What to show on screen:**
  - Keep the dashboard visible, or quickly flash the architecture diagram from `docs/index.html`.
  - Highlight the top right badge: `Reasoning Engine (us-central1)` and `PII Vault & Model Armor`.
- **What to say aloud:**
  > *"To ensure 100% mathematical fidelity, we never let the LLM guess split math. We built a deterministic Python physics engine and Wolverine L4 rate grid that snaps watts directly to stroke rates from 16 to 28 SPM.*
  > 
  > *The system is architected across Google Cloud's 4 agent pillars:*
  > 1. *Under **Build**, our ADK orchestrator manages specialized skills and subagents.*
  > 2. *Under **Scale**, it deploys to Vertex AI Reasoning Engine with durable sessions and Memory Bank persistence.*
  > 3. *Under **Govern**, it enforces GDPR Article 9 boundary sanitization with Cloud DLP, cryptographic pseudonyms, and Model Armor screening across all 4 execution checkpoints.*
  > 4. *Under **Optimise**, releases are guarded by fail-closed golden evaluation suites."*

---

### Scene 3: Live Interactive Demo (1:15 – 2:05) | 50 Seconds
- **What to do on screen:**
  1. Click the prompt chip: **`💡 Coach David's UT2 Base Session`**.
  2. Watch the response stream in real time. Point out:
     - The left **5-Stage Periodization Stepper** illuminating as tools run (`Season Curriculum` $\to$ `Physiology Rules` $\to$ `L4 Stroke Grid`).
     - The exact mathematical output: 1:58.2 benchmark split mapped to UT2 L4 @17 (2:25.3 / 117W).
  3. Click or paste: **`Can you generate the session sheets and coaching doc for the current week?`**
  4. Show the resulting **[Download Plan]** buttons appearing.
  5. Click one of the **[Download Plan]** buttons to demonstrate the live download of the markdown training sheet.
- **What to say aloud:**
  > *"Let's see it in action.*
  > 
  > *When Coach asks for a base session, the agent activates our deterministic physiology tools. Notice how the left periodization stepper moves in real time from season curriculum to physiology rules and L4 stroke grid.*
  > 
  > *It resolves Coach David's 1:58.2 benchmark split and computes the exact UT2 pace: 2:25.3 at 17 strokes per minute.*
  > 
  > *When requesting workout sheets, the agent generates localized French squad sheets and English methodology notes, with direct, one-click download buttons."*

---

### Scene 4: Enterprise Privacy Vault & Model Armor (2:05 – 2:40) | 35 Seconds
- **What to do on screen:**
  1. Click the **"PII Audit Vault"** tab in the right-hand drawer.
  2. Show **Gate 1: Boundary Sanitization**:
     - Point to the raw message sanitized to `[ATHLETE_ath_5a77195e]`.
     - Point to the green checklist: *Zero PII Leaked to LLM Weights*, *Reversible Client Rehydration*.
  3. Point to **Model Armor Screening**:
     - Template `rowing-coach-guardrails` active, screening direct and indirect prompt injections across all 4 checkpoints in the agent loop.
- **What to say aloud:**
  > *"Handling athlete data safely was our primary enterprise breakthrough.*
  > 
  > *In the PII Audit Vault tab on the right, you can see Gate 1 in action: pure-ASGI middleware strips real athlete names and domestic phone numbers into deterministic SHA-256 cryptographic tokens—like `[ATHLETE_ath_5a77195e]`.*
  > 
  > *Gemini never sees personal identity. Yet, because the token maps to our local squad database, our deterministic tools look up his 2k split and calculate the exact pacing.*
  > 
  > *Google Cloud Model Armor inspects the prompt at the edge and screens tool outputs before they enter LLM context, blocking indirect prompt injection. On egress, Gate 2 rehydrates the name back to the coach's screen with zero token leakage."*

---

### Scene 5: UI Polish & Hackathon Closing (2:40 – 3:00) | 20 Seconds
- **What to do on screen:**
  1. Click the **Theme Toggle button** in the top header (switch to Light mode, then back to Dark mode).
  2. Flash the GitHub repository or Documentation site.
  3. End on the clean dashboard.
- **What to say aloud:**
  > *"The UI includes responsive dark and light themes, an interactive approval gate for Intervals.icu calendar staging, and full mobile optimization.*
  > 
  > *All code, test suites with 80 passing tests, and interactive docs are open-sourced on GitHub. Thank you to Google Cloud and GDG London!"*

---

## 🏆 3. Why Cloud Run is Better than Local Frontend for the Video

| Factor | Cloud Run (`run.app`) | Local Frontend (`localhost`) |
| :--- | :--- | :--- |
| **Judge Credibility** | **Extremely High**: Visible `run.app` domain proves production GCP deployment | Moderate: Looks like a local mock |
| **Infrastructure Proof** | Shows live HTTPS, Cloud Run revision, and Vertex AI backend integration | Only shows local container |
| **Passcode Protection** | Built-in authentication via `?code=rowing-********-devcamp-2026` | No cloud auth demonstrated |
| **Safety Net** | If internet blips, switch immediately to `http://localhost:8080` | Local only |

> [!TIP]
> **Use Cloud Run as your primary recording screen.** Keep `./run-frontend.sh --local` running in your terminal on `http://localhost:8080` just in case you need a local fallback.
