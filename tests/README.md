### Technical Justification and Operational Trade-Offs

The unit test architecture for `DEBUG-AGENT-MVP` is designed to enforce complete isolation from external infrastructure while providing deterministic verification across all system components.

```
                                  [pytest harness]
                                         │
       ┌───────────────────┬─────────────┴─────────────┬───────────────────┐
       ▼                   ▼                           ▼                   ▼
[test_sandbox]      [test_mcp_db]              [test_indexer]      [test_orchestrator]
       │                   │                           │                   │
  Path Traversal      DQL Validation               AST Parsing       Lock Contention
  Container Flags     Mutation Rejection           Call Hierarchy    State Routing
  Timeout Bounds      Cursor Interception          Hash Invariance   Quota Failover

```

* **Hermetic Boundary Isolation**: Live daemon sockets (`/var/run/docker.sock`), database network endpoints (PostgreSQL, Qdrant), and remote API calls (Gemini LLM endpoints) are substituted with deterministic test doubles (`unittest.mock.patch`, `unittest.mock.MagicMock`). Container invocation logic, AST traversal, SQL validation grammar, and LangGraph routing topologies are tested without requiring ambient infrastructure.
* **Defensive Security Verification**: Path traversal mitigations (`Path.is_relative_to`), Git repository protection invariants (`.git` write prohibitions), and DQL validation regular expressions are verified against malicious input vectors to prevent container breakouts and unwanted mutations.
* **Operational Trade-Off**: Subprocess calls and container execution behavior are validated at the interface boundary rather than via live Docker daemon runs. Integration-level behavioral bugs attributable to specific host kernel configurations or Docker storage driver idiosyncrasies are decoupled from the unit testing layer to optimize execution velocity and determinism.
