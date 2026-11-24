#!/usr/bin/env bash
# Download data/radar.db from the `data` branch, if that branch exists yet.
# (The very first run starts with an empty database.)
set -euo pipefail
mkdir -p data
if git ls-remote --exit-code --heads origin data >/dev/null 2>&1; then
  git fetch --quiet --depth=1 origin data
  git show FETCH_HEAD:radar.db > data/radar.db
  echo "Loaded database from the data branch ($(du -h data/radar.db | cut -f1))."
else
  echo "No data branch yet - starting with an empty database."
fi
