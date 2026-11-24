#!/usr/bin/env bash
# Save data/radar.db to the `data` branch as ONE fresh commit (force-pushed).
#
# Why not add a commit every run? The database already keeps its own history
# (first-seen dates, closed items, page snapshots). Keeping every old binary
# copy in git would grow the repo by roughly a gigabyte a year. Recent copies
# are kept as workflow artifacts instead (see the "Back up database" step).
set -euo pipefail
DB="data/radar.db"
if [ ! -f "$DB" ]; then
  echo "No database to save."
  exit 0
fi

WORK="$(mktemp -d)"
# Leftovers from an earlier local run would make `checkout --orphan` fail.
git branch -D data-snapshot >/dev/null 2>&1 || true
# A worktree shares this checkout's git config, including push credentials.
git worktree add --quiet --detach "$WORK"
cp "$DB" "$WORK/radar.db.new"
(
  cd "$WORK"
  git checkout --quiet --orphan data-snapshot
  git rm -r --quiet --cached . >/dev/null
  find . -mindepth 1 -maxdepth 1 ! -name .git ! -name radar.db.new -exec rm -rf {} +
  mv radar.db.new radar.db
  printf '# data branch\n\nOnly `radar.db` (the Opportunity Radar SQLite database) lives here.\nGitHub Actions overwrites it after every run - do not commit here by hand.\n' > README.md
  git add radar.db README.md
  git -c user.name="opportunity-radar-bot" \
      -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit --quiet -m "data: update $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push --quiet --force origin HEAD:refs/heads/data
)
git worktree remove --force "$WORK"
git branch -D data-snapshot >/dev/null 2>&1 || true
echo "Saved database to the data branch ($(du -h "$DB" | cut -f1))."
