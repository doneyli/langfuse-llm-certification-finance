#!/usr/bin/env bash
#
# Switch (or fan out over) the active Langfuse environment.
#
# Every script in this repo — and the portal — loads `.env` with
# load_dotenv(override=True), so the .env FILE always wins over exported
# shell variables. That makes the file itself the swap lever: this script
# keeps two credential profiles side by side and repoints the `.env`
# symlink at one of them.
#
#   .env.local  -> self-hosted Langfuse (e.g. http://localhost:3001)
#   .env.cloud  -> Langfuse Cloud (https://cloud.langfuse.com or US region)
#
# Usage:
#   bash scripts/use_env.sh status        # show which profile is active
#   bash scripts/use_env.sh local         # point .env at .env.local
#   bash scripts/use_env.sh cloud         # point .env at .env.cloud
#   bash scripts/use_env.sh both -- uv run python run_certification.py ...
#                                         # run one command against EACH profile
#                                         # (sequentially; original restored after)
#
# Note: the portal reads .env once at startup — restart it after a switch.
set -euo pipefail

cd "$(dirname "$0")/.."

PROFILES=(local cloud)

active_profile() {
  if [ -L .env ]; then
    basename "$(readlink .env)" | sed 's/^\.env\.//'
  elif [ -e .env ]; then
    echo "standalone-file"
  else
    echo "none"
  fi
}

describe() {  # $1 = profile
  # `|| true` keeps set -e from silently killing the script when the profile
  # has no matching line (grep exits 1 on no match). LANGFUSE_HOST is the
  # alias cert_common.py honors as a fallback.
  local line
  line="$(grep -E '^LANGFUSE_(BASE_URL|HOST)=' ".env.$1" | head -1 || true)"
  echo "active Langfuse env: $1  (${line:-LANGFUSE_BASE_URL=unset})"
}

switch_to() {  # $1 = profile
  local p="$1"
  if [ ! -f ".env.$p" ]; then
    echo "error: .env.$p does not exist — create it first (see README" >&2
    echo "  'Keeping local and Cloud side by side')" >&2
    exit 1
  fi
  if [ -e .env ] && [ ! -L .env ]; then
    echo "error: .env is a regular file; refusing to replace it." >&2
    echo "  Keep it as your local profile, then re-run:" >&2
    echo "    mv .env .env.local && bash scripts/use_env.sh $p" >&2
    exit 1
  fi
  ln -sfn ".env.$p" .env
  describe "$p"
}

cmd="${1:-status}"
case "$cmd" in
  status)
    p="$(active_profile)"
    case "$p" in
      local|cloud) describe "$p" ;;
      *) echo "active Langfuse env: $p" ;;
    esac
    ;;
  local|cloud)
    switch_to "$cmd"
    ;;
  both)
    shift
    [ "${1:-}" = "--" ] && shift
    if [ $# -eq 0 ]; then
      echo "usage: bash scripts/use_env.sh both -- <command...>" >&2
      exit 1
    fi
    orig="$(active_profile)"
    restore() {
      case "$orig" in local|cloud) ln -sfn ".env.$orig" .env ;; esac
    }
    trap restore EXIT
    for p in "${PROFILES[@]}"; do
      echo
      echo "════════════ running against '$p' ════════════"
      switch_to "$p"
      "$@"
    done
    ;;
  *)
    echo "usage: bash scripts/use_env.sh {status|local|cloud|both -- <command...>}" >&2
    exit 1
    ;;
esac
