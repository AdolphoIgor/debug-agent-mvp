#!/usr/bin/env bash
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "================================================="
echo "        RUNNING actf-core PLATFORM TEST SUITE       "
echo "================================================="

DOCKER_FLAGS="-i -e PYTHONDONTWRITEBYTECODE=1"

echo -e "\n${GREEN}[1/5] Running Suite Tests...${NC}"
docker exec $DOCKER_FLAGS debug-agent-mvp \
  python3 -m pytest -o cache_dir=/tmp/.pytest_cache /opt/src/tests/ -v


echo -e "\n${GREEN}================================================="
echo -e "      ALL TEST SUITES PASSED SUCCESSFULLY!       "
echo -e "=================================================${NC}\n"