# ADR 0001: Hermetic Container Boundaries

* **Status:** Accepted
* **Date:** 2026-08-06
* **Deciders:** Data Platform Architecture Team

## Context
Orchestration (Airflow) and distributed execution (Ray) require isolated runtime environments to avoid dependency bloat, security risks, and filesystem coupling.

## Decision
Airflow (`actf-core-airflow-webserver`) and Ray (`actf-core-ray-head`/`worker`) operate in isolated containers. The `/orchestrator/dags` directory is mounted exclusively to Airflow, while subsystem code (`1-raw-data-ingest`, `2-data-prep`, `3-model-training`, `4-model-eval`) is mounted exclusively to Ray.

## Consequences
* **Positive:** Prevents transitive dependency conflicts and eliminates cross-mounting container file systems.
* **Negative:** Requires distinct container target calls during automated test execution.