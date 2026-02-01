# FARMA - Semi-Autonomous Directed AI-Powered Humanitarian & Agricultural Intelligence System for Food Insecurity in Nigeria

> Built for the Google Deepmind Gemini 3 Hackathon on Devpost

1. Problem Context
Nigeria is facing a severe food insecurity crisis. Current estimates suggest 30–35 million people are food insecure, with 1 in 5 children malnourished.Key drivers include:
* Armed conflict and banditry
* Internal displacement
* Climate volatility (droughts, floods)
* Food inflation
* Poor agricultural infrastructure
* Lack of access to credit for smallholder farmers
Smallholder farmers—who produce most of Nigeria’s food—are locked out of traditional finance:
* No formal credit history
* No verified land titles
* Fear of using land as collateral
* High uncertainty around yield
At the same time, humanitarian organizations and governments lack timely, verifiable intelligence to direct aid effectively to displaced populations.
This project addresses both sides of the problem:
1. Humanitarian intelligence for displacement and food risk
2. Accessible financing and decision support for smallholder farmers

2. High-Level Solution Overview
The system is designed to be an semi-autonomous, long-running (“marathon”) agentic system, aligned with Gemini 3’s strengths.
It consists of two tightly coupled subsystems:
A. Humanitarian Intelligence System
Continuously monitors conflict, displacement, inflation, and food risk signals across Nigerian states.
B. Farmer Support & Financing System
Enables farmers to:
* Request loans
* Report crop disease
* Receive climate and yield intelligence—all through SMS in local languages, without smartphones or data access.
Crucially, humanitarian intelligence feeds back into financial decision-making, enabling risk-aware loan disbursement and humane repayment restructuring.

3. Humanitarian Intelligence Architecture (What’s Already Built)
3.1 Agent Structure
The humanitarian system is composed of specialized agents, orchestrated via a graph-based workflow:
1. Data Collection Agents
    * Conflict data
    * Displacement data
    * Economic inflation data
    * Additional contextual signals
    * Uses Gemini + Google Search
    * Extracts:
        * Facts
        * Sources
        * URLs (for verification)
2. Data Intelligence Agent
    * Aggregates all raw outputs
    * Preserves source attribution
    * Outputs a structured, state-specific dataset
3. Synthesis Agent
    * Uses Gemini 3 Pro + Flash
    * Performs:
        * Risk assessment
        * Threat analysis
        * Displacement intensity analysis
    * Operates in a ReAct-style loop
    * Stores reasoning steps for auditability
4. Report Generation Agent
    * Produces a final humanitarian intelligence report
    * Includes:
        * Verified claims
        * Source citations
        * Risk summaries
    * Uses Nano Banana to generate high-quality infographics

3.2 State-Level Orchestration
* Each Nigerian state is treated as a worker node
* For each state:
    * All data collection tools are executed
    * Results are aggregated
    * Stored in the database
* This enables:
    * Weekly or periodic autonomous runs
    * Longitudinal tracking of risk trends

3.3 Data Storage Design
* Uses PostgreSQL
* Chosen to balance:
    * Relational structure
    * Semi-structured agent outputs
* Stores:
    * Raw collected data
    * Synthesis results
    * Reasoning traces
    * Source URLs

3.4 Safety & Auditability (Intentional Design Choice)
This is a high-stakes system. Errors can misdirect aid.
Therefore:
* Every agent action logs:
    * Inputs
    * Reasoning
    * Outputs
* Sources are always preserved
* Reports are fully verifiable
* The system explicitly avoids “black-box” hallucinations

4. Farmer Interaction System (What’s Already Built)
4.1 SMS-First Interface
Farmers interact only via SMS, in their local language or dialect.
Current supported intents:
* Loan requests
* Crop disease reports
No smartphones. No internet. No apps.

4.2 Crop Disease Diagnosis Flow
1. Farmer describes symptoms via SMS
2. Gemini Flash:
    * Extracts symptoms
    * Matches against a plant disease dataset
3. Gemini Pro:
    * Evaluates the diagnosis (verification step)
4. Final response sent to farmer:
    * Disease name
    * Practical treatment guidance
This uses an Evaluator–Optimizer pattern to reduce incorrect diagnoses.

4.3 Loan Intelligence & Farm Analysis
Core Challenge
Farmers cannot provide precise farm coordinates.
Current Solution
* Farmers describe landmarks via SMS
* Gemini extracts landmarks
* Google Maps used to infer coordinates
* If coordinates fail vegetation checks:
    * Fallback logic shifts coordinates nearby
* Once validated:
    * Google Earth Engine is used to compute:
        * NDVI
        * Rainfall (CHIRPS)
        * Satellite imagery (Sentinel, Copernicus)
This produces a remote farm viability report for lenders.

5. Intelligence ↔ Finance Feedback Loop (Key Innovation)
The humanitarian intelligence system directly informs financial decisions:
* If a state is flagged as high-risk:
    * New loans may be paused
* If instability rises after loans are issued:
    * Repayment plans can be restructured
* This:
    * Protects investors
    * Avoids punishing farmers during crises
    * Keeps the system economically viable

6. Intended Future Expansion (Explicitly Out of Scope for Prototype)
These are not fully built, but intentionally designed for:
* Community agents (e.g. NYSC members) to:
    * Capture precise farm coordinates
    * Act as trusted local verifiers
* Continuous monitoring agents that:
    * Track climate changes
    * Detect drought/flood risks early
    * Proactively notify farmers
* A fully autonomous weekly intelligence cycle
