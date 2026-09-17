# Autonomous Debugging Agent (MVP)

An enterprise-grade, agentic execution harness and retrieval-augmented reasoning system engineered to diagnose, patch, and deterministically verify software defects in isolated runtimes.

---

## Executive Overview

Software debugging at scale suffers from high mean time to resolution (MTTR) due to cognitive fatigue, context switching, and the complex dependency graphs of modern codebases. While large language models (LLMs) demonstrate significant code-generation capabilities, standard generation workflows operate open-loop: they propose static modifications without empirical validation, frequently introducing subtle regressions, hallucinating APIs, or failing to resolve subtle runtime errors.

The **Autonomous Debugging Agent MVP** solves this paradigm failure. It implements a closed-loop, stateful control system that unifies semantic code indexing, Model Context Protocol (MCP) data access, dynamic execution isolation, and cyclic graph-based agent orchestration. Instead of speculating on potential fixes, the platform actively reproduces failures, tests hypotheses within isolated ephemeral sandboxes, and iterates until deterministic test verification is achieved.

Designed with an emphasis on systems engineering, operational resilience, and defense-in-depth security, this project showcases production-ready architectural patterns for enterprise LLMOps and agentic automation.

---

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

---

## Architectural Deep Dive

The platform follows a layered, decoupled service architecture designed for high cohesion, strict isolation, and clean separation of concerns.

```
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

* Implements the standardized Model Context Protocol (MCP) over standard I/O (stdio).
* Provides client LLMs with uniform primitives to inspect schemas, execute parameterized queries, and query semantic similarity without exposing direct database credentials to the model.

### 4. Semantic Code Indexer (`src/code_indexer.py`)

Replaces naive sliding-window text chunking with context-aware semantic indexing:

* Parses repository structures to extract classes, functions, and docstrings.
* Computes vector representations of source blocks for pgvector storage, enabling pinpoint semantic retrieval of relevant symbols during root-cause localization.

### 5. High-Throughput Persistence Tier (`src/db_pool.py`, `database/schema.sql`)

* Backed by PostgreSQL with `pgvector` extensions for unified relational and vector storage.
* Managed via an asynchronous connection pool (`asyncpg` / `psycopg`) ensuring non-blocking operations, connection reuse, and resilience under concurrent execution loads.

---

## Technical Stack & Tooling

| Domain | Technology / Specification | Rationale |
| --- | --- | --- |
| **Language Runtime** | Python 3.11 | High performance, modern typing support, native async primitives. |
| **Package Management** | `uv` (Astral) | Sub-second deterministic resolution and lockfile synchronization (`uv.lock`). |
| **Agent Orchestration** | LangGraph / LangChain | Stateful, multi-actor cyclic graphs with typed checkpoints and conditional routing. |
| **Tool Protocol** | Model Context Protocol (MCP) | Vendor-agnostic, enterprise-standard schema for AI tool invocation. |
| **Database & Vectors** | PostgreSQL 16 + pgvector | Unified database architecture eliminating the overhead of dedicated vector stores. |
| **Isolation Barrier** | Docker Compose / Docker API | Hard sandbox isolation preventing host pollution during dynamic code execution. |
| **Developer Experience** | Devcontainers, VS Code Workspace | Zero-friction, container-standardized development and debugging configurations. |

---

## Directory Structure

```
.
├── .devcontainer/              # VS Code remote container development spec
├── .vscode/                    # Editor configurations, task runners, and debug targets
├── database/
│   └── schema.sql              # Relational schemas, pgvector indexes, and tables
├── docs/
│   └── adr/                    # Architecture Decision Records (ADRs)
├── src/
│   ├── code_indexer.py         # Structural code parsing, chunking, and embeddings
│   ├── db_pool.py              # Asynchronous PostgreSQL connection pool manager
│   ├── mcp_db_server.py        # Model Context Protocol service implementation
│   ├── orchestrator_graph.py   # LangGraph state machine and routing logic
│   ├── sandbox_engine.py       # Docker-based runtime isolation and command runner
│   └── test_smoke.py           # End-to-end integration and smoke test harness
├── compose.yaml                # Multi-service infrastructure orchestration
├── Dockerfile                  # Core agent engine image specification
├── Dockerfile.sandbox          # Ephemeral, unprivileged execution runtime image
├── pyproject.toml              # Project metadata, tool configurations, and dependencies
├── run_tests.sh                # Deterministic test execution pipeline
└── uv.lock                     # Cryptographically pinned dependency graph

```

---

## Setup & Operation

### Prerequisites

* Docker Engine 24.0+ and Docker Compose v2+
* Python 3.11+ (if running bare-metal)
* `uv` package manager (`curl -LsSf [https://astral.sh/uv/install.sh](https://astral.sh/uv/install.sh) | sh`)

### Environment Configuration

Copy the sample environment file and configure model credentials and database parameters:

```bash
cp .env-example .env

```

Ensure the following environment variables are supplied:

* `DATABASE_URL`: PostgreSQL connection string (e.g., `postgresql://postgres:postgres@localhost:5432/debug_agent`)
* Model provider credentials (e.g., `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or local endpoint configurations)

### Local Infrastructure Deployment

Launch the database, indexing backend, and supporting services using Docker Compose:

```bash
docker compose up -d postgres

```

Initialize schemas and vector extensions:

```bash
docker compose exec -T postgres psql -U postgres -d debug_agent < database/schema.sql

```

### Dependency Installation & Environment Sync

Synchronize the virtual environment using `uv`:

```bash
uv sync --frozen

```

### Building the Sandbox Environment

Build the unprivileged target runtime used for isolated test executions:

```bash
docker build -f Dockerfile.sandbox -t debug-agent-sandbox:latest .

```

---

## Execution & Verification

### Running the Smoke Test Suite

A comprehensive validation suite ensures that the database pool, MCP endpoints, sandbox container, and LangGraph state engine coordinate as expected:

```bash
./run_tests.sh

```

Or execute directly through `uv`:

```bash
uv run python -m pytest src/test_smoke.py -v

```

### Indexing a Codebase

To ingest and semantically index a source repository for agent context:

```bash
uv run python src/code_indexer.py --target-dir ./path/to/target-repo

```

### Executing the Orchestrator

To trigger an automated debugging loop against a known test failure:

```bash
uv run python src/orchestrator_graph.py --test-command "pytest tests/test_target.py"

```

---

## Architectural Decision Records (ADRs)

Key architectural decisions are documented to preserve institutional design rationale:

* **ADR-0001: Model Context Protocol (MCP) for Tooling**
*Decision:* Standardize database and retrieval interfaces over MCP rather than proprietary framework wrappers.
*Outcome:* Uncoupled agent logic from data storage, simplifying test mocking, security auditing, and protocol substitution.
* **ADR-0002: Dual-Container Execution Boundary**
*Decision:* Strict separation between the agent orchestrator container and the code evaluation sandbox (`Dockerfile.sandbox`).
*Outcome:* Mitigates container breakout risks, limits blast radius of untested code patches, and guarantees environment parity.
* **ADR-0003: Unified Vector & Relational Storage via pgvector**
*Decision:* Consolidate vector embeddings and relational agent traces in PostgreSQL instead of provisioning standalone vector databases.
*Outcome:* Reduced operational footprint, transaction-safe vector updates, and simplified infrastructure deployment.

---

## Engineering Quality & Production Readiness

* **Zero-Trust Runtime Execution:** Every command executed during regression testing runs inside a segregated container with dropped capabilities and process limits.
* **Non-Blocking I/O:** Asynchronous database access and subprocess pooling prevent thread starvation during long-running test suites.
* **Deterministic Reproducibility:** Hermetic dependency management via `uv.lock` and Docker multi-stage builds guarantee environment uniformity across local devcontainers, CI/CD runners, and production deployments.