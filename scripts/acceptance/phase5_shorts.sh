#!/usr/bin/env bash
# Phase 5 acceptance: five Shorts from the edited 10-minute interview.
# The finder proposes moments; the ranges below are the agent's choices after reading them
# (playbook 08): Short 1 extended to its payoff, Shorts 3-5 chosen for self-contained openings.
set -euo pipefail
cd "$(dirname "$0")/../.."
J=${1:-accept-nps-interview}
C=${CUTROOM:-cutroom}
MODE=${MODE:-preview}
$C shorts-find $J --count 5
make() {  # number, from, to, title, hook text, hook source sentence
  $C short-create $J --number "$1" --from "$2" --to "$3" --title "$4"
  S=$J.short-$(printf %02d "$1")
  $C captions $S --style bold-karaoke --output youtube_short
  $C short-hook $S --text "$5" --fact "sentences:$6"
  $C render $S --mode $MODE
  $C review $S
  $C short-publish $S
}
make 1 50.08 102.66   "The White Sands finding"           "People were here longer than we thought" 10
make 2 137.70 182.30  "Tracks everywhere"                 "Archaeologists typically don't look for tracks" 24
make 3 294.30 340.75  "Did humans kill off the megafauna?" "Did humans cause the extinction?" 59
make 4 362.26 386.50  "Sloths, mammoths and people"       "Ground sloths reacting to human tracks" 65
make 5 183.97 212.34  "Kids at the lake"                  "Tracks from kids, including toddlers" 32

# Agent steps after reading the reviews and transcripts (playbook 08):
# 1. Whisper heard "white sand" (low confidence) for the place name: fix it, re-plan captions.
$C fix-words $J --find "white sand" --replace "White Sands"
$C captions $J --style clean
$C captions $J.short-03 --style bold-karaoke --output youtube_short
$C render $J.short-03 --mode $MODE
# 2. Short 3 keeps one flash shot that cannot be fixed without changing the words (needs a J-cut).
$C review $J.short-03 --accept cuts.flash_shot@32.974 --reason "Sliver holds 'But did'; trimming changes the words, lengthening brings back a filler; needs a J-cut."
$C review $J.short-03
# 3. Publishing text written by the agent from each publish.task.md, then checked.
CREDIT="Footage: National Park Service (public domain)"
write() { printf '{"title": "%s", "description": "%s\\n\\n%s", "hashtags": %s}\n' "$2" "$3" "$CREDIT" "$4" > "jobs/$J.short-0$1/publish.json"; $C short-publish $J.short-0$1 --check; }
write 1 "People were at White Sands during the last glacial maximum" "National Park Service chief scientist Dan Odess on the White Sands finding: people here far earlier than confirmed anywhere else in the Americas, 8,000 to 10,000 years further back than most archaeologists accepted." '["#Shorts", "#Archaeology", "#WhiteSands", "#IceAge"]'
write 2 "At White Sands, the evidence is tracks" "Archaeologists typically don't look for tracks, but here human and mammoth tracks on top of each other show they shared the landscape." '["#Shorts", "#Archaeology", "#FossilTracks", "#Mammoth"]'
write 3 "Did humans cause the extinction of the megafauna?" "White Sands shows people overlapping with these animals for 10,000 years or more before the extinction. Did they hunt them? Probably. Did they cause the extinction? That seems less and less likely." '["#Shorts", "#Archaeology", "#Megafauna", "#WhiteSands"]'
write 4 "Ground sloths reacted to human tracks. Mammoths didn't care." "Fossil trackways show ground sloths reacting strongly when they encounter fresh human tracks, while mammoths seem unconcerned." '["#Shorts", "#GroundSloth", "#Mammoth", "#FossilTracks"]'
write 5 "Most of the tracks here were made by kids" "At the edge of a lake, most of the tracks come from kids, including toddlers." '["#Shorts", "#Archaeology", "#FossilTracks", "#WhiteSands"]'
# 4. Finals for delivery.
for n in 1 2 3 4 5; do $C render $J.short-0$n --mode final; done
