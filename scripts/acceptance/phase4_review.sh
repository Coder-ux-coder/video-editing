#!/usr/bin/env bash
# Phase 4 acceptance: plant defects in a clean edit, check the review finds every one, then let
# `review --fix` repair them. Usage: phase4_review.sh <job> <clean version>
set -euo pipefail
cd "$(dirname "$0")/../.."
J=${1:-accept-review}
CLEAN=${2:-v013}
C=${CUTROOM:-cutroom}
PY=${PYTHON:-python}
$C checkout $J $CLEAN
$PY scripts/acceptance/phase4_seed.py $J
$C review $J --json > "jobs/$J/review/detect.json"
$PY - "$J" <<'PYEOF'
import json, sys
job = sys.argv[1]
seeds = json.load(open(f"jobs/{job}/review/seeds.json"))["seeds"]
found = {i["check"] for i in json.load(open(f"jobs/{job}/review/detect.json"))["issues"]}
missed = [s for s in seeds if s["check"] not in found]
for s in seeds:
    print(("found " if s["check"] in found else "MISSED"), s["check"], "-", s["seed"])
sys.exit(1 if missed else 0)
PYEOF
$C review $J --fix --json > "jobs/$J/review/fix.json"
$PY - "$J" <<'PYEOF'
import json, sys
job = sys.argv[1]
res = json.load(open(f"jobs/{job}/review/fix.json"))
for r in res["rounds"]:
    print(f"round {r['round']}: v{r['version']:03d} {r['scorecard']['counts']}")
    for f in r["fixes"]:
        print("   ", f.get("applied") or f"FAILED {f['check']}: {f.get('failed')}")
print("ready for the human gate:", res["ready"], "| report:", res["report"])
sys.exit(0 if res["ready"] else 1)
PYEOF
