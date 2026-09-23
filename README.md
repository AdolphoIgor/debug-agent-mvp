# Autonomous Debugging Agent (MVP)

An enterprise-grade, agentic execution harness and retrieval-augmented reasoning system engineered to diagnose, patch, and deterministically verify software defects in isolated runtimes.

> **Notice: Experimental Prototype & API Requirements**
>
> * **Showcase Status**: This repository is an experimental Minimum Viable Product (MVP) engineered strictly for technical demonstration, architectural evaluation, and research purposes. It is not hardened for production infrastructure.
> * **API Key Prerequisite**: The system requires a valid **Google AI Studio API key** to execute LLM-driven agent routines. Supply your key via the `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) environment variable.
> * **Free-Tier Testing & Quota Efficiency**: It is strongly recommended to run integration tests and evaluations against the **Google AI Studio Free Tier**. The orchestration engine integrates a `DynamicFreeTierModelPool` (`src/gemini_quota_pool.py`) that dynamically queries available Gemini models, scores candidate endpoints (such as Gemini 1.5 Pro and Flash variants), and rotates through candidate models upon encountering quota boundaries (HTTP 429) to utilize daily free quotas efficiently without interrupting execution workflows.

## Executive Overview

Software debugging at scale suffers from high mean time to resolution (MTTR) due to cognitive fatigue, context switching, and the complex dependency graphs of modern codebases. While large language models (LLMs) demonstrate significant code-generation capabilities, standard generation workflows operate open-loop: they propose static modifications without empirical validation, frequently introducing subtle regressions, hallucinating APIs, or failing to resolve subtle runtime errors.

The **Autonomous Debugging Agent MVP** solves this paradigm failure. It implements a closed-loop, stateful control system that unifies semantic code indexing, Model Context Protocol (MCP) data access, dynamic execution isolation, and cyclic graph-based agent orchestration. Instead of speculating on potential fixes, the platform actively reproduces failures, tests hypotheses within isolated ephemeral sandboxes, and iterates until deterministic test verification is achieved.

Designed with an emphasis on systems engineering, operational resilience, and defense-in-depth security, this project showcases production-ready architectural patterns for enterprise LLMOps and agentic automation.

## Workflow Flowchart (English)

```mermaid
flowchart TD
    classDef startNode fill:#2563eb,stroke:#1d4ed8,color:#ffffff,stroke-width:2px;
    classDef syncNode fill:#7c3aed,stroke:#6d28d9,color:#ffffff,stroke-width:2px;
    classDef phase1Node fill:#0284c7,stroke:#0369a1,color:#ffffff,stroke-width:2px;
    classDef phase2Node fill:#059669,stroke:#047857,color:#ffffff,stroke-width:2px;
    classDef consultantNode fill:#d97706,stroke:#b45309,color:#ffffff,stroke-width:2px;
    classDef decisionNode fill:#f8fafc,stroke:#475569,color:#0f172a,stroke-width:2px;
    classDef successNode fill:#16a34a,stroke:#15803d,color:#ffffff,stroke-width:2px;

    UserInput(["User Request: issue_id & problem_statement"]):::startNode
    GitDelta{"Git Delta Detected in .py?"}:::decisionNode

    UserInput --> NodeGitSync["node_git_sync<br/>(Git pull & diff check)"]:::syncNode
    NodeGitSync --> GitDelta

    subgraph IndexingSubsystem ["Incremental Structural & Semantic Indexing"]
        TreeSitter["Tree-sitter AST Parser<br/>(Extract functions, classes & calls)"]:::syncNode
        PostgresSync[("PostgreSQL<br/>code_symbols & code_dependencies")]:::syncNode
        QdrantSync[("Qdrant Vector DB<br/>mvp_codebase Collection")]:::syncNode
    end

    GitDelta -- "Yes" --> TreeSitter
    TreeSitter -->|"Persist symbols & call edges"| PostgresSync
    TreeSitter -->|"FastEmbed symbols"| QdrantSync
    GitDelta -- "No" --> MCPServerReady

    PostgresSync --> MCPServerReady["FastMCP Code Intelligence Server<br/>(search_codebase, get_symbol_blast_radius, read_source_file)"]:::syncNode
    QdrantSync --> MCPServerReady

    MCPServerReady --> P1_Prog["node_programmer (Phase 1: Reproduction)<br/>- Queries codebase via MCP<br/>- Generates pytest with in-memory mocks<br/>- Modifies NO production code"]:::phase1Node

    subgraph Phase1 ["Phase 1: Reproduction & Test Locking"]
        P1_Auditor{"node_blind_auditor<br/>Zero-Context Inspection:<br/>Valid mocked pytest?"}:::decisionNode
        P1_Sandbox{"node_sandbox_execution<br/>Hermetic Container --network=none<br/>Does test fail on baseline?"}:::decisionNode
        LockTest["FREEZE REGRESSION TEST<br/>- is_test_locked = True<br/>- locked_test_code preserved<br/>- Reset stagnation & consultant counters"]:::phase1Node
    end

    P1_Prog --> P1_Auditor
    P1_Auditor -- "APPROVE" --> P1_Sandbox
    P1_Auditor -- "REJECT" --> P1_AuditStag{"Stagnation >= 3?"}:::decisionNode
    P1_AuditStag -- "No (Retry)" --> P1_Prog

    P1_Sandbox -- "Yes (Reproduced error)" --> LockTest
    P1_Sandbox -- "No (Passed on baseline or syntax error)" --> P1_SandStag{"Stagnation >= 3?"}:::decisionNode
    P1_SandStag -- "No (Retry)" --> P1_Prog

    LockTest --> P2_Prog["node_programmer (Phase 2: Resolution)<br/>- Locked test is IMMUTABLE<br/>- Generates unified git patch for prod code<br/>- Adds DDL/migration files if schema changed"]:::phase2Node

    subgraph Phase2 ["Phase 2: Resolution & Autonomous Publishing"]
        P2_Auditor{"node_blind_auditor<br/>Zero-Context Inspection:<br/>Safe patch? Locked test untouched?"}:::decisionNode
        P2_Sandbox{"node_sandbox_execution<br/>Hermetic Container --network=none<br/>Run locked test + full suite"}:::decisionNode
        Publish["node_publish_and_index<br/>- Create branch: fix/issue-{id}-{timestamp}<br/>- Stage prod patch, locked test & DDL<br/>- Commit & push autonomously"]:::successNode
    end

    P2_Prog --> P2_Auditor
    P2_Auditor -- "APPROVE" --> P2_Sandbox
    P2_Auditor -- "REJECT" --> P2_AuditStag{"Stagnation >= 3?"}:::decisionNode
    P2_AuditStag -- "No (Retry)" --> P2_Prog

    P2_Sandbox -- "All tests pass" --> Publish
    P2_Sandbox -- "Tests fail" --> P2_SandStag{"Stagnation >= 3?"}:::decisionNode
    P2_SandStag -- "No (Retry)" --> P2_Prog

    subgraph EscalationSystem ["Architectural Escalation"]
        Consultant["node_consultant<br/>- Analyzes failure logs & critiques<br/>- Queries MCP for structural redesign<br/>- Injects consultant_guidance<br/>- Resets stagnation counter"]:::consultantNode
    end

    P1_AuditStag -- "Yes (Threshold reached)" --> Consultant
    P1_SandStag -- "Yes (Threshold reached)" --> Consultant
    P2_AuditStag -- "Yes (Threshold reached)" --> Consultant
    P2_SandStag -- "Yes (Threshold reached)" --> Consultant

    Consultant -->|"Guidance for reproduction"| P1_Prog
    Consultant -->|"Guidance for resolution"| P2_Prog

    Publish --> FlowEnd(["Workflow Complete: Fix Published"]):::successNode
```

## The Engineering Challenge & Problem Space

Building an autonomous agent that touches production-grade code introduces critical challenges that naive LLM scripts cannot address:

1. **Context Fragmentation & Needle-in-a-Haystack Limits**
   Modern codebases exceed prompt context limits. Passing raw files indiscriminately introduces noise, degrading LLM reasoning.
   *Solution:* A structural semantic indexer (`code_indexer.py`) that extracts code constructs into PostgreSQL with vector embeddings, serving localized syntactic and semantic references on demand.

2. **Unsafe Dynamic Execution & Sandbox Escape Risks**
   Testing agent-generated code requires running arbitrary commands. Running unverified patches on host infrastructure invites container breakouts, runaway processes, and resource exhaustion.
   *Solution:* An ephemeral, isolated Docker execution boundary (`sandbox_engine.py` paired with `Dockerfile.sandbox`) with non-root privileges, strict timeout guarantees, and bound memory/CPU constraints.

3. **Compounding Hallucinations in Linear Chains**
   Linear pipelines (e.g., prompt -> code -> output) fail when an error occurs; the system has no corrective mechanism.
   *Solution:* A cyclic finite state machine powered by LangGraph (`orchestrator_graph.py`) implementing an evaluate-and-reflect feedback loop that inspects runtime tracebacks and adjusts patches iteratively.

4. **Tooling & Data Protocol Lock-in**
   Ad-hoc LLM function calling creates tight coupling between the model provider and local execution scripts.
   *Solution:* The Anthropic Model Context Protocol (`mcp_db_server.py`), exposing database operations, schema inspections, and vector retrievals as standardized, decoupled tool contracts.

## Architectural Deep Dive

The platform follows a layered, decoupled service architecture designed for high cohesion, strict isolation, and clean separation of concerns.

```text
+-------------------------------------------------------------------------+
|                        Orchestrator Graph                               |
|                      (LangGraph State Machine)                          |
+----+--------------------+-----------------------+---------------------+--+
     |                    |                       |                     |
     v                    v                       v                     v
+------------+   +------------------+   +-------------------+   +---------------+
| Code       |   | MCP Database     |   | Sandbox Engine    |   | LLM Inference |
| Indexer    |   | Server (MCP)     |   | (Docker Runner)   |   | Engine        |
+-----+------+   +--------+---------+   +---------+---------+   +-------+-------+
      |                   |                       |                     |
      | AST / Embeddings  | Tool Call / Vector    | Ephemeral Container | Hypotheses &
      v                   v                       | Diagnostics         | Patches
+-----------------------------------+             v                     |
| PostgreSQL + pgvector Cluster     |     +-------------------+         |
| (Async Connection Pool)           |     | Isolated Target   | <-------+
+-----------------------------------+     | Environment       |
                                          +-------------------+

```

### 1. Orchestrator State Machine (`src/orchestrator_graph.py`)

The system core is a compiled LangGraph workflow modeling the debugging lifecycle as a directed cyclic graph with strictly typed state transitions:

* **Ingest & Reproduce:** Runs baseline test commands within the sandbox to capture raw tracebacks and verify reproducibility.

* **Locate & Retrieve:** Queries semantic indices and file hierarchies to pinpoint failing modules, functions, and cross-file dependencies.

* **Hypothesize & Formulate:** Generates a minimal, focused patch addressing the identified root cause.

* **Validate & Reflect:** Applies the patch in the sandbox and re-executes tests. If failures persist, standard output and error streams are fed back into the agent context for dynamic self-correction up to a configured threshold.

### 2. Isolated Execution Sandbox (`src/sandbox_engine.py`, `Dockerfile.sandbox`)

Untrusted runtime code execution is segregated into isolated, disposable execution environments:

* **Container-Level Isolation:** Leverages Docker APIs to run code independently of the host orchestrator.

* **Bounded Resource Allocation:** Enforces deterministic execution windows via explicit timeouts, mitigating infinite loops and out-of-memory crashes.

* **Deterministic Diagnostics:** Aggregates stdout, stderr, and exit codes into typed payloads returned directly to the state machine.

### 3. Model Context Protocol Server (`src/mcp_db_server.py`)

Decouples agent logic from persistence:

* Implements the standardized Model Context Protocol (MCP) over Server-Sent Events (SSE).

* Provides client LLMs with uniform primitives to inspect schemas, execute parameterized queries, and query semantic similarity without exposing direct database credentials to the model.

### 4. Dynamic Free-Tier Model Pool (`src/gemini_quota_pool.py`)

Provides resilient LLM client access against daily quota ceilings:

* Automatically discovers and ranks available Gemini models from the Google GenAI SDK.

* Detects quota exhaustion (HTTP 429) across nodes and routes requests dynamically to healthy fallback models in the pool.

### 5. Semantic Code Indexer (`src/code_indexer.py`)

Replaces naive sliding-window text chunking with context-aware semantic indexing:

* Parses repository structures using tree-sitter AST queries to extract classes, functions, and docstrings.

* Computes vector representations of source blocks for storage, enabling pinpoint semantic retrieval of relevant symbols during root-cause localization.

### 6. High-Throughput Persistence Tier (`src/db_pool.py`, `database/schema.sql`)

* Backed by PostgreSQL with relational schemas and vector extensions.

* Managed via a connection pool (`psycopg2`) ensuring non-blocking operations, connection reuse, and resilience under concurrent execution loads.

## Technical Stack & Tooling

| Domain | Technology / Specification | Rationale |
| --- | --- | --- |
| **Language Runtime** | Python 3.11 | High performance, modern typing support, native async primitives. |
| **Package Management** | `uv` (Astral) | Sub-second deterministic resolution and lockfile synchronization (`uv.lock`). |
| **Agent Orchestration** | LangGraph / LangChain | Stateful, multi-actor cyclic graphs with typed checkpoints and conditional routing. |
| **LLM Inference** | Google GenAI SDK (`google-genai`) | Native integration with Gemini models and dynamic quota pool management. |
| **Tool Protocol** | Model Context Protocol (MCP) | Vendor-agnostic, enterprise-standard schema for AI tool invocation. |
| **Database & Vectors** | PostgreSQL 16 & Qdrant | Hybrid relational schema and fast vector similarity indexing. |
| **Isolation Barrier** | Docker Compose / Docker API | Hard sandbox isolation preventing host pollution during dynamic code execution. |

## Directory Structure

```text
.devcontainer/              # VS Code remote container development spec
.vscode/                    # Editor configurations, task runners, and debug targets
database/
  schema.sql                # Relational schemas and index definitions
docs/
  adr/                      # Architecture Decision Records (ADRs)
src/
  code_indexer.py           # Structural code parsing, chunking, and embeddings
  db_pool.py                # Asynchronous PostgreSQL connection pool manager
  gemini_quota_pool.py      # Dynamic Gemini model discovery and quota fallback pool
  mcp_db_server.py          # Model Context Protocol service implementation
  orchestrator_graph.py     # LangGraph state machine and routing logic
  sandbox_engine.py         # Docker-based runtime isolation and command runner
  test_smoke.py             # End-to-end integration and smoke test harness
compose.yaml                # Multi-service infrastructure orchestration
Dockerfile                  # Core agent engine image specification
Dockerfile.sandbox          # Ephemeral, unprivileged execution runtime image
pyproject.toml              # Project metadata, tool configurations, and dependencies
run_tests.sh                # Deterministic test execution pipeline
uv.lock                     # Cryptographically pinned dependency graph

```

## Setup & Operation

### Prerequisites

* Docker Engine 24.0+ and Docker Compose v2+

* Python 3.11+ (if running bare-metal)

* `uv` package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

* A valid Google AI Studio API Key (Free tier recommended)

### Environment Configuration

Copy the sample environment file and configure model credentials and database parameters:

```bash
cp .env-example .env

```

Ensure the following environment variables are supplied in `.env`:

* `GEMINI_API_KEY`: Your personal Google AI Studio key.

* `DATABASE_URL`: PostgreSQL connection string (`postgresql://mvp_user:mvp_password@postgres:5432/mvp_db`).

* `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`: Credentials matching your database deployment.

* `QDRANT_URL`: Vector database host URL (`http://qdrant:6333`).

### Local Infrastructure Deployment

Launch the database, vector store, and supporting sidecars using Docker Compose:

```bash
docker compose up -d postgres qdrant db-init mcp-server record-proxy

```

### Dependency Installation & Environment Sync

Synchronize dependencies inside the virtual environment using `uv`:

```bash
uv sync --frozen

```

### Building the Sandbox Environment

Build the unprivileged target runtime image utilized for isolated test executions:

```bash
docker build -f Dockerfile.sandbox -t mvp-sandbox:latest .

```

## Execution & Verification

### Running the Smoke Test Suite

Execute the integration validation suite to verify the database pool and structural AST indexing:

```bash
./run_tests.sh

```

Or execute directly through `uv`:

```bash
uv run python -m pytest tests/test_smoke.py -v

```

### Executing the Orchestrator

To trigger an automated debugging loop against an isolated target ticket:

```bash
uv run python src/orchestrator_graph.py

```

## Architectural Decision Records (ADRs)

Key architectural decisions are documented to preserve institutional design rationale:

* **ADR-0001: Model Context Protocol (MCP) for Tooling**: Standardize database and retrieval interfaces over FastMCP rather than proprietary wrappers.

* **ADR-0002: Dual-Container Execution Boundary**: Enforce strict separation between the orchestrator container and the evaluation sandbox (`Dockerfile.sandbox`) to prevent container breakout vulnerabilities.

* **ADR-0003: Ephemeral Copy-on-Write Database Clones**: Provision disposable test databases via `TEMPLATE` cloning during sandbox executions and drop them on teardown.

## Engineering Quality & Production Readiness

* **Zero-Trust Runtime Execution:** Every command executed during regression testing runs inside a segregated container with dropped capabilities (`--cap-drop=ALL`) and unprivileged user context.

* **Resilient Quota Allocation:** Dynamic pool cycling allows continuous operation across multi-round agent reflection without failing on individual model quota spikes.

* **Deterministic Reproducibility:** Hermetic dependency management via `uv.lock` and Docker multi-stage builds guarantee environment uniformity across local devcontainers, CI/CD runners, and host execution.
