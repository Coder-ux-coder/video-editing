#!/usr/bin/env bash
# Phase 6 acceptance: b-roll, music, effects and a thumbnail on the interview; a style learned
# from a reference video. Run after phase5_shorts.sh (the long-form at HEAD has captions and graphics).
set -euo pipefail
cd "$(dirname "$0")/../.."
J=${1:-accept-nps-interview}
C=${CUTROOM:-cutroom}
# Public-domain stills of the trackways (licence read from Commons' metadata).
$C asset-fetch --query "White Sands fossil footprints" --limit 8 --pick 1,2,3,6 --tag tracks --tag footprints --tag track
$C broll-suggest $J
$C broll-add $J "$($C assets --json | python3 -c "import json,sys;print([a['id'] for a in json.load(sys.stdin)['assets'] if 'trench' in a['id']][0])")" \
   --at 6.2 --duration 4.0 --move in --reason "the excavation while he states the finding"
$C broll-add $J "$($C assets --json | python3 -c "import json,sys;print([a['id'] for a in json.load(sys.stdin)['assets'] if 'human-fossil-tracks' in a['id']][0])")" \
   --at 101.9 --duration 4.5 --move left --reason "human tracks as he lists them"
$C broll-add $J "$($C assets --json | python3 -c "import json,sys;print([a['id'] for a in json.load(sys.stdin)['assets'] if 'white-sands-fossil-footprints' in a['id']][0])")" \
   --at 122.6 --duration 3.6 --move out --reason "the tracks under 'the evidence is tracks'"
# Music under the cold open and the ending; a whoosh on the overlay transition.
$C music $J --generate documentary --from 0 --to 22
D=$($C show $J --json | python3 -c "import json,sys;print(round(json.load(sys.stdin)['duration_s'],2))")
$C music $J --generate documentary --from "$(python3 -c "print($D-20)")" --to "$D"
$C sfx $J --kind whoosh --transitions
$C review $J --accept cuts.flash_shot@281.723 --reason "Sliver holds 'But did'; trimming changes the words, lengthening brings back a filler sound; needs a J-cut."
$C review $J
$C thumbnails $J --text "Here longer than we thought" -e longer -f sentences:10
$C thumbnails $J --text "Here longer than we thought" --pick 2
# A style learned from a reference (any talking-head video you may use, in references/).
$C analyze-reference references/weekly_address_1080p.mov --name weekly-address --force
