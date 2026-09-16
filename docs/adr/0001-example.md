# ADR 0001: Single-Host Multi-Service Topology, MCP Sidecar, and Copy-on-Write Sandbox Execution

* **Status:** Accepted
* **Date:** 2026-09-16
* **Deciders:** DEBUG-AGENT-MVP Architecture & Engineering Team

## Context

The automated multi-agent debugging workflow requires a self-contained, reproducible runtime environment to parse codebases via AST, perform semantic RAG retrieval, inspect runtime database state safely, and execute regression test suites without compromising host integrity or relying on extraneous distributed dependencies. Previous architectural iterations contained unneeded components (Redis), mutable direct database access from agent prompts, unhardened test runners, and multi-tenant schema overhead that conflicted with a lean, single-run demonstration architecture.

## Decision

A single-host multi-container topology is established on a dedicated bridge network (`mvp_global_net`) orchestrated via `compose.yaml` and parameterized through a centralized `.env` configuration:

1. **Storage Decoupling and Schema Minimization**: Relational persistence and call-graph dependencies are maintained exclusively within PostgreSQL 16 (`code_symbols`, `code_dependencies`), while semantic symbol embeddings are managed within Qdrant (`mvp_codebase`). Redis is eliminated entirely. Multi-tenant entities (`clients`, `projects`, `ticket_tracking`, `ticket_semantic_vectors`) are purged from the relational DDL.
2. **Read-Only Database Inspection via FastMCP Sidecar**: Direct SQL execution access by LLM agents is prohibited. Database introspection is delegated to a dedicated FastMCP sidecar (`mvp_mcp_server`) communicating over Server-Sent Events (SSE), restricting operations to read-only DQL queries (`SELECT`) against `client_baseline_db`.
3. **Hardened Copy-on-Write (CoW) Test Sandboxing**: Ephemeral test execution is isolated inside a dedicated sandbox container (`Dockerfile.sandbox`, tagged as `mvp-sandbox:latest`). Tests are executed under an unprivileged identity (`sandboxuser`, UID 1000) with Linux capabilities stripped (`--cap-drop=ALL`), `no-new-privileges` enabled, and network egress routed through an HTTP record-and-playback proxy. Database state mutations during test runs are confined to short-lived PostgreSQL clones created via `CREATE DATABASE ... TEMPLATE client_baseline_db` and dropped upon completion.
4. **Execution Concurrency and Runtime Tooling**: The development workspace container (`app`) interacts with the host Docker daemon via `/var/run/docker.sock` to provision ephemeral sandbox containers out-of-band. Single-run confinement is enforced using an OS-level POSIX advisory file lock (`/tmp/mvp_orchestrator.lock`).
5. **Idempotent Bootstrapping & Packaging**: An initialization container (`db-init`) ensures baseline database creation and checks for table existence (`code_symbols`) prior to applying `database/schema.sql`. Packaging is standardized on PEP 621 utilizing `hatchling` and deterministic dependency locking via `uv`.

## Consequences

* **Positive:** Complete elimination of arbitrary SQL mutation vectors from agent prompts; container escape attack surfaces are mitigated through unprivileged, ephemeral sandbox execution; deterministic test isolation is achieved without leftover state artifacts; container layer dimensions and build latencies are minimized.
* **Negative:** Exposing the host Docker socket (`/var/run/docker.sock`) to the primary workspace container is required for ephemeral container instantiation; launching ephemeral containers introduces a minor latency overhead (~1–2 seconds) per validation cycle; multi-ticket concurrency is blocked by design under the single-run locking model.