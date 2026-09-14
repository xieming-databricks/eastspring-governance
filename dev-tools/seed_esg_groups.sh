#!/usr/bin/env bash
# seed_esg_groups.sh — create the five ESG ACCOUNT groups config/esg grants reference.
#
# Unity Catalog resolves grant principals at the ACCOUNT level, so these MUST be account groups:
# workspace-local groups fail a UC grant with PRINCIPAL_DOES_NOT_EXIST. The profile must be the
# account that OWNS the target workspace (adb-7405619633086661 / xieming-azure is owned by
# account azure-account-ccb842e7). Run BEFORE `mode=apply` (grants to a missing principal fail).
#
# Usage:  ./dev-tools/seed_esg_groups.sh [account-profile]   # default: azure-account-ccb842e7
# Idempotent: existing account group names are looked up ONCE up front and skipped.

set -uo pipefail
PROFILE="${1:-azure-account-ccb842e7}"

ESG_GROUPS=(
  hera-all-esg-read-unmasked
  hera-all-esg-read-masked
  hera-sg-esg-read-masked
  hera-sg-eq-read-masked
  hera-sg-fi-read-masked
)

# Guard: ACCOUNT host only (accounts.*) — UC grants need account-level groups, not workspace-local.
HOST="$(databricks auth describe --profile "$PROFILE" 2>/dev/null | awk -F': ' '/^Host:/{print $2; exit}')"
case "${HOST:-}" in
  *accounts.*) : ;;   # account host — ok
  *)           echo "REFUSING: '$PROFILE' (${HOST:-unknown}) is not an ACCOUNT host; UC grants need account-level groups."; exit 2 ;;
esac

# Existing account group names — fetched ONCE, then used to skip.
existing="$(databricks account groups list --profile "$PROFILE" -o json 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); rows=d if isinstance(d,list) else d.get('Resources',[]); print('\n'.join(str(x.get('displayName') or '') for x in rows))")"

for g in "${ESG_GROUPS[@]}"; do
  if printf '%s\n' "$existing" | grep -Fxq -- "$g"; then
    echo "exists   $g"
  elif databricks account groups create --display-name "$g" --profile "$PROFILE" >/dev/null 2>&1; then
    echo "created  $g"
  else
    echo "FAILED   $g (check the profile has account-admin group-create permission)"
  fi
done
