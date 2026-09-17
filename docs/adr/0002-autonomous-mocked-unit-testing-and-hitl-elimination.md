# docs/adr/0002-autonomous-mocked-unit-testing-and-hitl-elimination.md

# [DEBUG-AGENT-MVP] ADR-0002: AUTONOMOUS MOCKED UNIT TESTING AND HITL ELIMINATION

## Status
Accepted

## Context
In previous revisions, test validation relied upon a Copy-on-Write PostgreSQL database (cloned from a baseline template), proxy network infrastructure, and multiple Human-in-the-Loop (HITL) interruption gates. When executing repairs against arbitrary user-provided repositories, pre-configured database environments cannot be assumed. Furthermore, end-to-end regression testing and browser emulation require extensive exogenous telemetry data that is unavailable in an MVP scope. The retention of manual human arbitration between agent turns and after successful test runs introduces significant operational latency.

## Decision
1. **Hermetic Unit Testing with Mocks**: Regression testing against live external databases and telemetry-based replay is discontinued. The programmer agent is instructed to write isolated unit tests utilizing standard mocking techniques (`unittest.mock`, `pytest-mock`).
2. **Sandbox Engine Decoupling**: Database provisioning (`provision_cow_database`, `teardown_cow_database`) and outbound HTTP proxies are removed from `PythonCoWSandbox`. The container runner is refactored as `PythonHermeticSandbox`, enforcing `--network=none`.
3. **Elimination of Agent-Level HITL**: The human conciliation node (`node_human_audit_conciliation`) is removed. Auditor rejections are fed directly into the programmer feedback state, with escalation to `node_consultant` governed by an automated stagnation threshold.
4. **Autonomous Resolution on Test Clearance**: The final human inspection gate (`node_final_hitl_inspection`) is eliminated. If all unit tests pass in the hermetic sandbox, changes are committed and pushed autonomously, and the solution is returned to the user.

## Consequences
- **Positive**: Setup requirements for external services are eliminated; arbitrary Python repositories can be evaluated immediately; execution is fully automated end-to-end; network egress security risks are completely mitigated.
- **Negative**: Defects dependent on real database engine behavior, concurrency locks, or multi-service networking cannot be caught by mocked unit tests alone.
