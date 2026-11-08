#!/usr/bin/env bash
# Phase 8 acceptance: an interrupted render of the interview resumes from its finished parts and
# passes every self-check; a batch file runs and, run again, repeats nothing; the job report
# shows where the time went and the cost; the golden end-to-end test passes.
set -euo pipefail
cd "$(dirname "$0")/../.."
J=${1:-accept-nps-interview}
C=${CUTROOM:-cutroom}
JOB=${CUTROOM_JOBS_DIR:-jobs}/$J
OUT=${OUT:-$(mktemp -d)}
HEAD=$($C versions "$J" --json | python3 -c "import json,sys;print(next(v['version'] for v in json.load(sys.stdin)['versions'] if v['head']))")
TMP=$JOB/renders/$(printf ".tmp_v%03d_preview" "$HEAD")
rm -rf "$TMP"; rm -f "$JOB/renders/$(printf "v%03d_preview" "$HEAD").json"

# 1. Start a full preview render; interrupt it once the audio, the overlay
#    frames and the first video run are finished, 15 s into the next run; run the same command again.
$C render "$J" --mode preview --json > "$OUT/first.json" 2> "$OUT/first.err" &
PID=$!
python3 - "$TMP" <<'EOF'
import json, sys, time
from pathlib import Path
st = Path(sys.argv[1]) / "state.json"
while True:
    try:
        if len(json.loads(st.read_text()).get("runs_done", [])) >= 1:
            time.sleep(15)
            break
    except (OSError, ValueError):
        pass
    time.sleep(2)
EOF
kill -TERM "$PID"; wait "$PID" || true      # (a script's background jobs ignore SIGINT; TERM is handled like Ctrl-C)
echo "interrupted; kept: $(ls "$TMP" | tr '\n' ' ')"
T0=$(date +%s)
$C render "$J" --mode preview --json > "$OUT/resumed.json"
echo "resumed render took $(( $(date +%s) - T0 )) s"
python3 - "$OUT/resumed.json" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
assert r.get("resumed"), "the render did not resume"
assert all(r["checks"].values()), r["checks"]
print("resumed:", r["path"], r["size"], f"{r['duration_s']:.1f}s", "checks all pass;", "stages:", r["timings_s"])
EOF

# 2. A batch file; run twice (the second run skips everything).
cat > "$OUT/night.txt" <<EOF
# Phase 8 acceptance batch
validate $J
export $J
report $J
EOF
$C batch "$OUT/night.txt"
$C batch "$OUT/night.txt" --json | python3 -c "import json,sys;r=json.load(sys.stdin);assert r['skipped']==3,r;print('second run: all 3 lines skipped')"

# 3. The job report.
$C report "$J" | sed -n '1,40p'

# 4. Golden end-to-end regression.
.venv/bin/python -m pytest -q tests/test_golden.py
