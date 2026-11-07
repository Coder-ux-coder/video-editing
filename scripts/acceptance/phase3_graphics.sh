#!/usr/bin/env bash
# Phase 3 acceptance: graphics on the 10-minute NPS interview, from the captioned v069.
set -euo pipefail
cd "$(dirname "$0")/../.."
# Set CUTROOM_BROWSER to a Chrome Headless Shell if Remotion should not download its own.
J=${1:-accept-nps-interview}   # a job already through Phase 2 (captions at v069)
C=${CUTROOM:-cutroom}
$C graphic-add $J lower-third --version v069 --sentence 1 --variant bar -p "name=Dan Odess" \
   -p "title=Chief Scientist, National Park Service" -f sentences:1 -f sentences:4 \
   --reason "introduce the speaker when he first gives his name"
$C graphic-add $J number-counter --sentence 13 --duration 4 --variant big -p to=8000 -p "suffix=+ years" \
   -p "label=further into the past" -f sentences:13 --reason "the headline finding, as a number"
if $C graphic-add $J number-counter --sentence 13 --variant big -p to=12000 -f sentences:13; then
  echo "ERROR: an invented number was accepted"; exit 1
fi
$C graphic-add $J list-reveal --sentence 19 --duration 6 --variant bullets -p "title=Tracks found here" \
   -p "items=Human|Sloth|Mammoth|Wolf|Bear" -f sentences:19-20 --reason "the variety of tracks, as he names them"
$C graphic-add $J kinetic-text --sentence 25 --duration 2.8 --variant marker -p "text=The evidence is tracks" \
   -p "emphasis=tracks" -f sentences:25 --reason "punchline"
$C punch-in $J --auto
$C transition $J --at 248.3 --variant dip --reason "topic change: did humans kill off the megafauna"
$C graphic-add $J subscribe-cta --at 500 --variant subscribe --reason "end-of-video call to action"
$C graphic-add $J progress-bar --variant bottom --reason "viewer orientation"
$C validate $J
$C graphic-list $J
$C render $J --mode preview
$C style $J --client-style demo-bold
$C render $J --mode preview --range 70-78
