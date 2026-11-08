#!/usr/bin/env bash
# Phase 7 acceptance: a client revision round, publishing text, the final render, review, NLE
# export and the delivery package (AWAITING APPROVAL). Run after phase6_media.sh.
#
# The agent's judgement steps are files, as in the playbooks: the revision plan
# (review/revisions/r01/plan.json), publish.json and questions.md. This script stops where the
# agent has to write one of them and says so; it never runs `cutroom approve` (a person does).
set -euo pipefail
cd "$(dirname "$0")/../.."
J=${1:-accept-nps-interview}
C=${CUTROOM:-cutroom}
JOB=${CUTROOM_JOBS_DIR:-jobs}/$J
SEEN=$($C versions "$J" --json | python3 -c "import json,sys;print(next(v['version'] for v in json.load(sys.stdin)['versions'] if v['head']))")

# 1. Client notes on the version they watched → a revision round (times mapped to the current edit).
NOTES=$(mktemp)
cat > "$NOTES" <<'EOF'
Notes on the cut you sent (v103):
- 0:19 Can the name card say "Dr. Dan Odess"?
- The music at the very start is a little loud.
- 2:03 keep the footprints picture up a bit longer, it's a great image.
- 8:06 the part about the military co-use area feels off-topic for our audience, please cut it.
- Love the ending, keep it.
EOF
if [ ! -f "$JOB/review/revisions/r01/result.json" ]; then
  [ -d "$JOB/review/revisions/r01" ] || $C revise "$J" --notes "$NOTES" --seen "$SEEN"
  if python3 -c "import json,sys;p=json.load(open(sys.argv[1]));sys.exit(0 if all(n['commands'] or n['response'] for n in p['notes']) else 1)" \
       "$JOB/review/revisions/r01/plan.json"; then
    $C revise "$J" --apply r01
  else
    echo "Agent step: fill $JOB/review/revisions/r01/plan.json (playbook 10, step 8), then run this again."; exit 2
  fi
fi
# Words moved: captions again; then the review loop.
$C captions "$J"
$C review "$J" --fix

# 2. Publishing text after the last edit (publish.json is the agent's; --check validates it).
$C publish "$J"
[ -f "$JOB/publish.json" ] || { echo "Agent step: write $JOB/publish.json from publish.task.md, then run this again."; exit 2; }
$C publish "$J" --check

# 3. Final render (1080p x264), review reusing it, NLE export, package.
$C render "$J" --mode final
$C review "$J"
$C export "$J"
[ -f "$JOB/questions.md" ] || echo "Note: no questions.md; write one if anything needs the client's answer."
$C deliver "$J"
$C next "$J"
echo "Package prepared, AWAITING APPROVAL. A person watches it and runs \`cutroom approve\`."
