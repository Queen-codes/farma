<h1 align="center">FARMA</h1>

> Built for the Google Deepmind Gemini 3 Hackathon on Devpost

<p align="center">
  <strong>When 32 million people are food insecure, intelligence can't wait for Monday morning.</strong>
</p>

<p align="center">
  <em>A semi-autonomous marathon agent system that continuously monitors humanitarian crises across Nigerian states, self-corrects its own predictions day-over-day, and enables smallholder farmers to access loans via SMS — all powered by Gemini 3's Thought Signatures and Thinking Levels.</em>
</p>

<p align="center">
  <a href="#demo">View Demo</a> &middot;
  <a href="#architecture">Architecture</a> &middot;
  <a href="#marathon-agent">Marathon Agent</a> &middot;
  <a href="#gemini-integration">Gemini 3 Integration</a> &middot;
  <a href="#getting-started">Getting Started</a>
</p>

---

## The Problem

Nigeria is facing one of the world's most severe food insecurity crises:

| Metric | Scale |
|--------|-------|
| Food insecure population | **30–35 million people** |
| Child malnutrition rate | **1 in 5 children** |
| Key drivers | Armed conflict, displacement, climate volatility, food inflation |
| Smallholder farmer credit access | Near zero — no credit history, no land titles, no collateral |

Humanitarian organizations generate weekly intelligence reports. By the time a report is compiled, reviewed, and distributed, the situation on the ground has already changed. Displacement surges, conflict escalates, markets collapse — and the intelligence is stale before it arrives.

Meanwhile, smallholder farmers — who produce the majority of Nigeria's food — are locked out of traditional finance. They have no smartphones, no internet access, and no formal credit history. They farm on land they can describe by landmarks, not GPS coordinates.

**These aren't separate problems. They're the same system failure.**

When humanitarian intelligence is slow, aid is misdirected. When farmers can't access credit, food production drops. When food production drops, food insecurity deepens. FARMA breaks this cycle.

---

## What FARMA Does

FARMA is two tightly coupled systems with a critical feedback loop between them:

<img src="docs/assets/overview.png" alt="FARMA System Overview" width="100%"> 

### 1. AEGIS — Autonomous Humanitarian Intelligence

A 5-stage pipeline that scans, synthesizes, simulates, reports, and continuously monitors humanitarian conditions across Nigerian states — autonomously, day after day.

### 2. Farmer Support — SMS-First Financial Access

Farmers interact entirely via SMS in local languages. No apps. No internet. No smartphones required. They can request loans, report crop disease, and receive climate intelligence.

### 3. The Feedback Loop

Humanitarian intelligence directly informs financial decisions. If AEGIS detects escalating conflict in a state, new loans are paused or a small percentage is approved. If instability rises after loans are issued, repayment plans are restructured. The system protects both farmers and lenders — simultaneously.

---

## Hackathon Track

> **The Marathon Agent**: Build autonomous systems for tasks spanning hours or days. Use Thought Signatures and Thinking Levels to maintain continuity and self-correct across multi-step tool calls without human supervision.

FARMA doesn't just use the marathon agent concept — it's the architecture. The system runs weekly intelligence cycles autonomously, preserving its reasoning across days via Gemini 3's thought signatures, detecting when its own predictions were wrong, adjusting its thinking depth, and deciding when to escalate.

---

<a id="architecture"></a>
## Architecture

### System Architecture Overview

<img src="docs/assets/farma_architecture.png" alt="System Architecture">


### AEGIS Pipeline — Detailed Flow

```mermaid
flowchart TB
    subgraph SCAN["Stage 1: SCAN — Parallel Data Collection"]
        direction LR
        S1[Borno Worker] & S2[Adamawa Worker] & S3[Yobe Worker] & S4[Bauchi Worker] & S5[Gombe Worker] & S6[Taraba Worker]

        subgraph TOOLS["Grounded Search Tools"]
            T1["🔍 Conflict Events"]
            T2["🔍 IDP Displacement"]
            T3["🔍 Food Security (IPC)"]
            T4["🔍 Market Status"]
        end

        S1 & S2 & S3 & S4 & S5 & S6 --> TOOLS
    end

    subgraph SYNTH["Stage 2: SYNTHESIS — Intelligence Processing"]
        direction LR
        A1["Per-State Assessment\n(schema-constrained JSON)"]
        A2["National Rollup\n(priority rankings)"]
        A3["Metric Computation\n(deterministic scores)"]
        A1 --> A2 --> A3
    end

    subgraph SIM["Stage 3: SIMULATION — Crisis Projection"]
        direction LR
        P1["Scenario Detection"]
        P2["7/30/90-Day Projections"]
        P3["Policy Brief Generation"]
        P1 --> P2 --> P3
    end

    subgraph REPORT["Stage 4: REPORT — Knowledge Product"]
        direction LR
        R1["Narrative Synthesis"]
        R2["Infographic Generation"]
        R3["OCHA-Style PDF"]
        R1 --> R2 --> R3
    end

    subgraph MARATHON["Stage 5: MARATHON — Continuity Agent"]
        direction TB
        M1["Load Previous\nContinuity Note"]
        M2["Compute\nDeterministic Deltas"]
        M3["Generate Note\n(Extended Thinking +\nThought Signatures)"]
        M4{"Self-Correct\nPredictions"}
        M5["Decide Actions\n& Next Thinking Level"]
        M6["Persist Note\n+ Thought Signature"]

        M1 --> M2 --> M3 --> M4 --> M5 --> M6
        M6 -.->|"Next Day"| M1
    end

    SCAN --> SYNTH
    SYNTH --> SIM
    SYNTH --> REPORT
    SYNTH --> MARATHON
    MARATHON -.->|"Autonomous Trigger"| SIM
    MARATHON -.->|"Autonomous Trigger"| REPORT
```

### Farmer Workflow — SMS to Decision

```mermaid
flowchart LR
    SMS["📱 Farmer SMS\n(Local Language)"] --> PARSE["Intent\nExtraction"]
    PARSE --> GATE{"Intent\nClassification"}

    GATE -->|Loan Request| LOAN
    GATE -->|Disease Report| DISEASE
    GATE -->|Climate Query| CLIMATE
    GATE -->|Unrecognized| HUMAN["👤 Human\nEscalation"]

    subgraph LOAN["Loan Assessment"]
        L1["Landmark → Geocode\n(Google Maps)"]
        L2["Satellite Analysis\n(Earth Engine:\nNDVI + CHIRPS)"]
        L3["AEGIS Risk Check\n(Humanitarian Flags)"]
        L4["Underwriter\nDecision"]
        L1 --> L2 --> L3 --> L4
    end

    subgraph DISEASE["Disease Diagnosis"]
        D1["Symptom Extraction\n(Gemini Flash)"]
        D2["Diagnosis\nMatching"]
        D3["Verification\n(Gemini Pro)"]
        D1 --> D2 --> D3
    end

    subgraph CLIMATE["Climate Advisory"]
        C1["Location Geocode"]
        C2["Weather Forecast\n(Open-Meteo)"]
        C3["Rainfall History\n(CHIRPS)"]
        C4["Advisory\nGeneration"]
        C1 --> C2 --> C3 --> C4
    end

    LOAN --> RESP["📱 SMS Response\n(Local Language)"]
    DISEASE --> RESP
    CLIMATE --> RESP
    HUMAN --> RESP
```

---

<a id="marathon-agent"></a>
## The Marathon Agent — How It Actually Works

This is the core innovation. Most AI systems produce one-shot outputs. FARMA's marathon agent runs continuously, remembers what it said the previous week, checks if it was right, and adjusts.

<img src="docs/assets/marathon.png" alt="Marathon Timeline" width="100%">

### The Continuity Loop

Each week after a fresh scan, the marathon agent:

1. **Loads its previous continuity note** from the database — including last predictions
2. **Resolves the latest scan** and retrieves fresh intelligence data
3. **Computes deterministic deltas** — what changed between each week at every level (state risk levels, IDP counts, IPC phases, conflict events, priority rankings)
4. **Generates a new continuity note** using Gemini 3 with extended thinking, replaying its prior thought signature for reasoning continuity
5. **Self-corrects** — compares the last seven days predictions against the new actual data, explicitly logging where it was wrong
6. **Decides its own thinking depth** for the next scan — `"high"` if the situation is novel, `"medium"` if corrections were needed, `"low"` if things are stable
7. **Autonomously triggers actions** — if it detects a significant escalation, it fires off a simulation run or emergency report without human intervention

### Thought Signatures — Cross-Session Memory

Gemini 3 returns an opaque `thought_signature` with each response — a binary blob encoding the model's reasoning state. FARMA:

- Extracts and base64-encodes the thought signature after each generation
- Stores it in PostgreSQL alongside the continuity note
- Replays it as prior model content in the next day's call

This gives the agent genuine reasoning continuity across sessions. It doesn't just see the last week's output — it inherits its thinking context.

### Self-Correction in Practice

```
Day 1: "I predict conflict intensity in Borno will stabilize given
        recent ceasefire reports."

Day 2: "SELF-CORRECTION: My prediction about Borno stabilization was
        wrong. Conflict events increased by 40%. Revising assessment
        to CRITICAL. Recommending emergency simulation."

        next_thinking_level: "high"
```

### Continuity Note Schema

Every marathon day produces a structured `ContinuityNote`:

| Field | Purpose |
|-------|---------|
| `summary` | Today's situation assessment |
| `key_changes` | Notable deltas with source citations |
| `predictions` | 2-4 concrete predictions for tomorrow (verifiable) |
| `self_corrections` | Where yesterday's predictions were wrong |
| `next_thinking_level` | `"high"` / `"medium"` / `"low"` — adaptive reasoning depth |
| `decision_explanation` | First-person narration of the agent's reasoning |
| `recommended_actions` | Actions with source URIs |
| `confidence` | 0.0 – 1.0 confidence score |

### Thinking Levels — Adaptive Depth

The marathon agent doesn't use the same reasoning depth every week. It dynamically selects:

| Level | When Used | Effect |
|-------|-----------|--------|
| `"high"` | Novel situation, or significant escalation detected | Deep extended thinking for complex analysis |
| `"medium"` | Self-corrections needed or moderate changes observed | Balanced depth for course-correction |
| `"low"` | Stable situation, predictions confirmed | Efficient routine analysis |

---

<a id="gemini-integration"></a>
## Gemini 3 Integration — Full Breakdown

FARMA leverages Gemini 3 across every layer of the system. This isn't a wrapper around a chat API — it's a deeply integrated architecture where Gemini's specific capabilities enable features that wouldn't be possible otherwise.

### Features Used and Where

| Gemini 3 Feature | Where It's Used | Why It Matters |
|---|---|---|
| **Extended Thinking** | Marathon continuity notes, Synthesis assessments | Enables multi-step reasoning for complex humanitarian analysis |
| **Thought Signatures** | Marathon day-over-day continuity | Preserves reasoning context across sessions — the agent literally picks up where it left off |
| **Thinking Levels** (`low`/`medium`/`high`) | Marathon adaptive depth, Synthesis | Agent self-selects reasoning depth based on situation complexity |
| **Schema-Constrained JSON Generation** | Assessments, Continuity notes, Rollups, Loan decisions | Ensures deterministic, parseable outputs for high-stakes decisions |
| **Grounded Search** | Scan data collection (conflict, displacement, food security, economics) | All intelligence claims are backed by verifiable web sources |
| **Function Calling** | Scan planning — agent selects which tools to run per state | Dynamic tool selection based on context |
| **Flash Model** (`gemini-3-flash-preview`) | SMS parsing, scan planning, quick disease analysis | Fast inference for real-time farmer interactions |
| **Pro Model** (`gemini-3-pro-preview`) | Synthesis, marathon thinking, loan underwriting, diagnosis verification | Deep reasoning for high-stakes decisions |

### Model Deployment Strategy

```
gemini-3-flash-preview    → Speed-critical paths (SMS parsing, scan planning, quick triage)
gemini-3-pro-preview      → Reasoning-critical paths (synthesis, marathon, underwriting)
```

### Schema-Constrained Generation — Why It Matters Here

In a system where outputs gate humanitarian aid decisions and loan approvals, "the model usually returns valid JSON" isn't good enough. FARMA enforces Pydantic schemas at the Gemini API level:

```python
cfg_kwargs["response_json_schema"] = CONTINUITY_NOTE_SCHEMA
cfg_kwargs["response_mime_type"] = "application/json"
```

If schema-constrained generation fails, the system retries with JSON-only mode and validates against the Pydantic model as a fallback — never silently accepting malformed output.

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **AI** | Gemini 3 Pro, Gemini 3 Flash| Reasoning, analysis, grounded search |
| **Orchestration** | LangGraph | Graph-based workflow with parallel execution, conditional routing |
| **Backend** | FastAPI (Python 3.12) | Async API with streaming job events |
| **Database** | PostgreSQL + JSONB (Cloud Sql)| Relational structure with flexible agent output storage |
| **Geospatial** | Google Earth Engine | NDVI vegetation analysis, CHIRPS rainfall, satellite imagery |
| **Geocoding** | Google Maps Platform | Landmark-to-coordinate inference for farmers |
| **Weather** | Open-Meteo API | 14-day forecast for climate advisories |
| **Frontend** | React 19, TypeScript, Vite, Recharts | Pipeline visualization, marathon timeline, dashboard |
| **PDF Generation** | ReportLab | OCHA-style humanitarian intelligence reports |
| **Deployment** | Docker, Google Cloud Run | Containerized production deployment |
| **Storage** | Google Cloud Storage | Report distribution and artifact persistence |

---

## Key Features

### AEGIS Intelligence Dashboard

- Real-time aggregate intelligence across monitored states
- Conflict event counts, displacement estimates, food insecurity phases
- State priority rankings with trend indicators


### AEGIS Pipeline — 5-Stage Orchestration

<!-- INSERT: Pipeline UI screenshot here -->
<!--  <img src="docs/assets/pipeline.png" alt="AEGIS Pipeline" width="100%"> -->

- One-click "Run Demo" orchestrator that chains all 5 stages
- Stage-by-stage readiness guards — downstream stages are disabled until prerequisites complete
- Real-time streaming event timeline for each running job
- LocalStorage persistence — refresh the page without losing state

### Marathon Continuity Timeline

- Visual timeline of daily continuity notes
- Predictions vs. self-corrections
- Decision explanations in the agent's own words
- Thinking level indicators showing adaptive depth

### Farmer SMS Simulation

<!-- INSERT: Farmer simulation UI screenshot here -->
<!-- <img src="docs/assets/farmer_sim.png" alt="Farmer Simulation" width="100%"> -->

- SMS template builder for testing loan, disease, and climate flows
- Toggle AEGIS humanitarian intelligence on/off to see its impact on loan decisions
- Job timeline showing each workflow step
- Human escalation flow for unrecognized intents

### PDF Reports
- Professional humanitarian intelligence reports with verified citations
- State-by-state annexes with risk metrics
- Infographic visualizations with text-only fallback
- Downloadable via authenticated API endpoint

---

## Project Structure

```
farma/
├── app/
│   ├── main.py                      # FastAPI entry point
│   ├── config.py                    # Model versions, concurrency, focus states
│   │
│   ├── aegis/                       # Humanitarian Intelligence System
│   │   ├── scan/                    # Parallel data collection (6 state workers)
│   │   │   ├── state_worker.py      # Per-state tool planning + execution
│   │   │   ├── grounding.py         # Gemini grounded search integration
│   │   │   └── tools/               # conflict, displacement, food_security, economic
│   │   ├── synthesis/               # Schema-constrained intelligence processing
│   │   ├── simulator/               # Crisis projection + policy briefs
│   │   ├── report/                  # PDF generation
│   │   ├── marathon/                # Day-over-day continuity agent
│   │   │   ├── nodes.py             # LangGraph nodes for marathon flow
│   │   │   ├── llm.py              # Thought signature replay + continuity generation
│   │   │   ├── schema.py           # ContinuityNote Pydantic schema
│   │   │   └── deltas.py           # Deterministic delta computation
│   │   └── db/                      # PostgreSQL models + async connection
│   │
│   ├── workflows/                   # Farmer Support System
│   │   ├── graph.py                 # LangGraph: SMS → intent → analysis → response
│   │   ├── nodes/
│   │   │   ├── parsers/             # SMS + voice input parsing
│   │   │   ├── loan/                # Geocode → satellite → AEGIS check → underwrite
│   │   │   ├── disease/             # Symptom extraction → diagnosis → verification
│   │   │   ├── climate/             # Geocode → forecast → CHIRPS → advisory
│   │   │   └── human/              # Human escalation for edge cases
│   │   ├── gee_signals.py          # Google Earth Engine integration
│   │   └── geocode_provenance.py   # Landmark → coordinate inference
│   │
│   └── api/                         # FastAPI routes
│       ├── routes/
│       │   ├── aegis.py             # Full pipeline endpoints (2000+ lines)
│       │   ├── farmer.py            # SMS/loan endpoints
│       │   └── jobs.py              # Job status + event streaming
│       └── schemas.py               # All Pydantic request/response contracts
│
├── frontend/                        # React 19 + TypeScript + Vite
│   ├── App.tsx                      # Three-tab layout (Dashboard, Farmer, Pipeline)
│   └── components/
│       ├── Dashboard.tsx            # Intelligence overview
│       ├── FarmerSimulation.tsx     # SMS test harness
│       └── AegisPipeline.tsx        # 5-stage pipeline with marathon timeline
│
├── scripts/
│   ├── smoke_demo.sh                # End-to-end demo validation
│   └── preflight_submission.sh      # Pre-submission checks
│
└── docs/                            # Architecture documentation
```

---

## Safety & Auditability — By Design

This is a high-stakes system. Errors can misdirect humanitarian aid or wrongly deny farmers credit. Every architectural decision reflects this:

| Principle | Implementation |
|-----------|---------------|
| **No black-box outputs** | Every claim in every report cites verifiable source URIs from grounded search |
| **URI whitelisting** | Marathon continuity notes can only reference URIs that appeared in the original scan — no hallucinated citations |
| **Schema enforcement** | All agent outputs are validated against Pydantic schemas — malformed output is caught and retried, never silently accepted |
| **Reasoning traces** | Every agent action logs inputs, reasoning, and outputs to PostgreSQL |
| **Self-correction transparency** | Marathon agent explicitly publishes where its predictions were wrong |
| **Human escalation** | Unrecognized farmer intents route to human operators — the system knows when to say "I don't know" |
| **Deterministic deltas** | changes are computed programmatically, not generated — reproducible and auditable |

---

<a id="demo"></a>
## Demo

<!-- INSERT: Demo video link or embedded GIF here -->
<!--  [![FARMA Demo](docs/assets/demo_thumbnail.png)](https://youtu.be/your-demo-link) -->

### Running the Demo

The one-click demo orchestrator chains all 5 AEGIS stages:

**Scan** → **Synthesis** → **Simulation** → **Report** → **Marathon**

Each stage streams real-time progress events to the UI. The marathon stage generates a continuity note, makes predictions, and — on subsequent runs — self-corrects against its previous predictions.

---

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 18+
- PostgreSQL
- Google API Key (Gemini 3 access)
- Google Earth Engine service account
- Google Maps API key

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/farma.git
cd farma

# Backend setup
pip install -e .

# Frontend setup
cd frontend && npm install && cd ..

# Environment configuration
cp .env.example .env
# Edit .env with your API keys and database URL

# Initialize database
python scripts/init_db.py

# Start backend
uvicorn app.main:app --reload

# Start frontend (separate terminal)
cd frontend && npm run dev
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_API_KEY` | Gemini 3 API key |
| `DATABASE_URL` | PostgreSQL connection string |
| `service_account` | Earth Engine service account email |
| `THINKING_LEVEL` | Default thinking level (`low`/`medium`/`high`) |
| `AEGIS_FOCUS_STATES` | Comma-separated Nigerian states to monitor |
| `MAX_STATE_WORKERS` | Parallel scan concurrency (default: 8) |

---

## API Endpoints

### AEGIS Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/aegis/scan` | Trigger parallel state data collection |
| `POST` | `/api/aegis/synthesis` | Process raw data into structured assessments |
| `POST` | `/api/aegis/simulations` | Run crisis projection + policy brief |
| `POST` | `/api/aegis/report` | Generate OCHA-style PDF report |
| `POST` | `/api/aegis/marathon/run` | Execute marathon continuity day |
| `POST` | `/api/aegis/demo/run` | One-click full pipeline orchestration |
| `GET`  | `/api/aegis/dashboard` | Aggregate intelligence dashboard |
| `GET`  | `/api/aegis/pipeline/readiness/{scan_id}` | Stage-by-stage readiness check |
| `GET`  | `/api/aegis/marathon/{track_id}/timeline` | Continuity note timeline |
| `GET`  | `/api/aegis/report/{id}/download` | Download PDF report |

### Farmer System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/farmer/simulate` | Start farmer SMS simulation (loan/disease/climate) |
| `POST` | `/api/farmer/{job_id}/resume` | Resume a human-interrupted farmer job |

---

## Built With

- [Google Gemini 3](https://deepmind.google/technologies/gemini/) — Extended thinking, thought signatures, grounded search, schema-constrained generation
- [LangGraph](https://github.com/langchain-ai/langgraph) — Graph-based agent orchestration
- [FastAPI](https://fastapi.tiangolo.com/) — Async Python backend
- [Google Earth Engine](https://earthengine.google.com/) — Satellite-based farm viability analysis
- [Google Maps Platform](https://developers.google.com/maps) — Landmark geocoding
- [React](https://react.dev/) — Frontend with real-time pipeline visualization
- [PostgreSQL](https://www.postgresql.org/) — Persistent intelligence + continuity storage
- [ReportLab](https://www.reportlab.com/) — PDF report generation

---

## License

This project was built for the Google DeepMind Gemini 3 Hackathon on Devpost.

<!-- INSERT: Team photo or headshots here -->
<!-- Example: <img src="docs/assets/team.png" alt="Team" width="400"> -->
