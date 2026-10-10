# Enterprise Agentic AI Platform for Supply Chain & Logistics

A multi-layer AI platform for running enterprise supply chain workflows with governed LLM access, agent collaboration, graph-based reasoning, and evaluation-first design.

This repository brings together a digital twin, an AI gateway, agent execution workflows, a GraphRAG knowledge layer, and an evaluation stack to solve real operational problems in procurement, logistics, supplier disruption management, and execution oversight.

## Why this project exists

Most enterprise AI pilots fail because they stop at a demo chatbot. In real supply chain environments, teams need:

- Safe access to LLMs across multiple providers and tenants
- Agents that can act on operational workflows, not just answer questions
- A shared source of truth for orders, vendors, plants, documents, and signals
- Retrieval grounded in real business context
- Observability so decisions can be audited and measured
- Evaluation to verify that automation works under normal and failure conditions

This project is our answer to those problems. Instead of building isolated AI experiments, we build a connected system where each layer has a clear role.

## The problem we are solving

Supply chain teams deal with decisions that are high-risk, time-sensitive, and deeply connected:

- Which supplier should receive a purchase request?
- Who should approve a PO and under what conditions?
- What happens when a supplier slips or a disruption hits a region?
- How do we know which recommendation is grounded in real business data?
- How do we prevent AI from hallucinating facts, bypassing policy, or acting without traceability?

The agents in this repository are designed to help with those problems by coordinating decisions, seeking evidence from enterprise data, and operating under a shared policy and evaluation framework.

## What the platform includes

### 1. AI Gateway
The gateway is the controlled entry point for all model access. It handles routing, tenant-aware access, budgets, caching, rate limiting, fallback behavior, and cost tracking.

This prevents each app or agent from directly calling provider SDKs and ensures every LLM interaction is governed and measurable.

### 2. Multi-Agent Platform
This layer executes operational workflows such as purchase-to-pay and disruption response. Agents collaborate across sourcing, procurement, logistics, and finance responsibilities.

The core idea is not "one AI that answers everything", but a set of specialized agents that can:

- evaluate supplier bids
- issue or revise purchase orders
- rebook shipments
- coordinate exceptions
- escalate to humans when policy requires it

### 3. Supply Chain Digital Twin
This is the shared world model for the system. It contains the enterprise context: master data, orders, inventory, vendors, plants, documents, and live or synthetic external signals.

The twin is not an AI feature; it is the operational environment the AI agents reason over.

### 4. GraphRAG Assistant
This layer connects unstructured and structured knowledge through graph + retrieval patterns. It helps answer questions about supplier relationships, risk, document lineage, and business context using citations and graph traversal.

This matters when the answer depends on more than a single record or a simple search.

### 5. Evaluation Platform
The evaluation layer is critical. It checks whether workflows actually do what they are supposed to do and whether changes introduce regressions.

It measures things like:

- workflow success
- policy compliance
- cost and latency
- agent behavior under failure conditions
- result quality across scenarios and goldens

### 6. Operating Platform
This is the enterprise-facing layer that composes the stack into a usable experience and exposes operational dashboards, execution controls, and workflow visibility.

---

## System architecture

```text
                  ┌──────────────────────────────┐
                  │ Enterprise AI Operating      │
                  │ Platform                     │
                  │ orchestration / control      │
                  └──────────────┬───────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        ▼                        ▼                        ▼
   AI Gateway             Multi-Agent Platform       GraphRAG Assistant
   model access            decision + action         reasoning + retrieval
        │                        │                        │
        └────────────────────────┼────────────────────────┘
                                 │
                                 ▼
                       Supply Chain Digital Twin
                        master data + transactions + docs
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
                  ERP        Documents      Signals
              orders / POs      PDFs        disruptions / data
```

The design principle is that each layer is independently useful and deployable. The gateway has value without agents, evaluation works across agents, and the twin provides the shared world view that everything else operates on.

---


## Example workflows in this repo

### Purchase-to-pay execution
The platform demonstrates a deterministic purchase workflow with AI-assisted decision support.

A typical flow includes:

1. PR created for a material need
2. Sourcing evaluates vendor bids
3. Procurement decides the purchase order
4. Logistics books the carrier
5. Finance matches the invoice and closes the transaction
6. Exception paths trigger rebooking, escalation, or human-in-the-loop state transitions

This is implemented to show how agent coordination can handle both the happy path and the exception path without losing control, traceability, or policy enforcement.

### Supplier disruption management
The platform also targets disruption scenarios, such as a supplier outage or a regional disruption.

A disruption workflow can involve:

- identifying affected orders and suppliers
- checking lead times and inventory cover
- evaluating alternate vendors
- recommending a reroute or expedited response
- escalating only when a business decision requires human approval

This is the kind of real-world problem that makes the combination of agent orchestration + graph knowledge + evaluation important.

---

## Repository layout

```text
Logistics_Agent_SCM/
├── README.md
├── docker-compose.yml
├── AI_Gateway/
├── Multi_Agent_Platform/
├── Enterprise_graphrag/
├── Agent_Evaluation_Platform/
├── Enterprise_AI_Platform/
├── Supply_Chain_Digital_Twin/
└── .env
```

Each component has its own plan and implementation area, but they are meant to be deployed and operated together as one platform.

---

## Local setup

### Prerequisites

Before running the platform locally, make sure you have:

- Docker Desktop or Docker Engine
- Docker Compose
- Python 3.10+ or the version used by the project venv
- PowerShell on Windows or bash on Linux/macOS
- Access to a local model runtime such as Ollama, if you want gateway-local inference
- PostgreSQL, Redis, and Neo4j services available through Docker or local installation

### Quick start with Docker

From the repo root:

```powershell
pwsh -File scripts/up.ps1 -Build
```

Then open the operating platform in a browser:

```text
http://127.0.0.1:8010
```

See the deployment guide in [docs/deploy.md](docs/deploy.md) for more details on the full service topology and environment setup.

### Run services manually

If you prefer to run the pieces individually, use the project venv and launch each service in sequence:

1. AI Gateway on port 8000
2. Multi-Agent Platform on port 8001
3. GraphRAG service on port 8100
4. Evaluation service on port 8002
5. Digital Twin on port 8003
6. Operating Platform on port 8010

Example commands from the project root or the component directories:

```powershell
# AI Gateway
# run from AI_Gateway/

# Multi-Agent Platform
# run from Multi_Agent_Platform/
python -m api.main

# GraphRAG
# run from Enterprise_graphrag/
PYTHONPATH=. python -m graphrag.cli reindex
PYTHONPATH=. python -m graphrag.cli serve

# Digital Twin
# run from Supply_Chain_Digital_Twin/
python -m twin.cli serve

# Operating Platform
# run from Enterprise_AI_Platform/
python -m os_platform.cli stack --attach
```

The exact command set may vary by service and environment, so the component-level plans and deployment docs should be treated as the source of truth.

---

## Development principles

This project follows a few important operating principles:

- Model access goes through the gateway, never directly from components
- Retrieval is owned by the GraphRAG layer, not ad hoc in each app
- All major activity is traced and attributable
- Data lives in a shared digital twin rather than scattered copies across agents
- Evaluation is part of the system, not an afterthought
- Agents are designed for real execution workflows and exception handling, not just text generation



## License

This repository is intended for demonstration, experimentation, and internal platform engineering. License terms should be checked in the project’s deployment and organizational context before production use.
