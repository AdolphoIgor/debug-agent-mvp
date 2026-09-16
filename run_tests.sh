#!/usr/bin/env bash
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

LOG_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --log=*)
      LOG_FILE="${1#*=}"
      shift
      ;;
    --log)
      if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
        LOG_FILE="$2"
        shift 2
      else
        echo -e "${RED}[ERROR] Missing file path for --log option.${NC}" >&2
        exit 1
      fi
      ;;
    *)
      echo -e "${RED}[ERROR] Unknown argument: $1${NC}" >&2
      echo "Usage: ./run_tests.sh [--log <path/to/logfile.log>]" >&2
      exit 1
      ;;
  esac
done

if [[ -n "$LOG_FILE" ]]; then
  mkdir -p "$(dirname "$LOG_FILE")"
  exec > >(tee -a "$LOG_FILE") 2>&1
  echo "[INFO] Test suite output is being logged to: $LOG_FILE"
fi

echo "================================================="
echo "        RUNNING actf-core PLATFORM TEST SUITE       "
echo "================================================="

DOCKER_FLAGS="-i -e PYTHONDONTWRITEBYTECODE=1"

echo -e "\n${GREEN}[1/5] Running Layer 1: Airflow DAG Integrity & Suite Tests...${NC}"
docker exec $DOCKER_FLAGS actf-core-airflow-webserver \
  python3 -m pytest -o cache_dir=/tmp/.pytest_cache /opt/airflow/tests/ -v

echo -e "\n${GREEN}[2/5] Running Layer 2: Raw Ingestion Unit Tests...${NC}"
docker exec $DOCKER_FLAGS actf-core-ray-head \
  bash -c "cd /home/ray/workspace/1-raw-data-ingest && PYTHONPATH=ray/scripts:spark/scripts:. uv run --all-extras pytest tests/ -v -o cache_dir=/tmp/.pytest_cache"

echo -e "\n${GREEN}[3/5] Running Layer 3: Data Prep Unit Tests...${NC}"
docker exec $DOCKER_FLAGS actf-core-ray-head \
  bash -c "cd /home/ray/workspace/2-data-prep && PYTHONPATH=scripts:. uv run --all-extras pytest tests/ -v -o cache_dir=/tmp/.pytest_cache"

echo -e "\n${GREEN}[4/5] Running Layer 4: Model Training Unit Tests...${NC}"
docker exec $DOCKER_FLAGS actf-core-ray-head \
  bash -c "cd /home/ray/workspace/3-model-training && PYTHONPATH=scripts:. uv run --extra cpu pytest tests/ -v -o cache_dir=/tmp/.pytest_cache"

echo -e "\n${GREEN}[5/5] Running Layer 5: Model Evaluation & Gatekeeper Unit Tests...${NC}"
docker exec $DOCKER_FLAGS actf-core-ray-head \
  bash -c "cd /home/ray/workspace/4-model-eval && PYTHONPATH=scripts:. uv run --extra cpu pytest tests/ -v -o cache_dir=/tmp/.pytest_cache"

echo -e "\n${GREEN}================================================="
echo -e "      ALL TEST SUITES PASSED SUCCESSFULLY!       "
echo -e "=================================================${NC}\n"