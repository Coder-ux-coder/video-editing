# Agent playbook

Reusable instructions a Claude session loads and follows when editing. Start with
`00_orchestrator.md`; it names the playbook for each stage. Each playbook covers purpose,
inputs, procedure (exact `cutroom` commands), decision rules, outputs, failure handling and a checklist.

| File | Stage | Status |
|---|---|---|
| `00_orchestrator.md` | Master loop, gates, when to ask | Written (Phase 1) |
| `01_intake.md` | Brief, ingest, analyze, job plan | Written (Phase 1) |
| `02_transcript_review.md` | Names, terms, sensitive content | Written (Phase 1) |
| `03_story_and_cut_plan.md` | Topics, hook, structure, cut plan | Written (Phase 1) |
| `04_rough_cut.md` | Rough cut and verification | Written (Phase 1) |
| `05_audio.md` | Voice chain and loudness | Phase 2 |
| `06_graphics_and_captions.md` | Captions and motion graphics | Phases 2–3 |
| `07_broll_and_music.md` | B-roll, music, SFX | Phase 6 |
| `08_shorts.md` | Shorts | Phase 5 |
| `09_review_loop.md` | Automatic review and fixes | Phase 4 |
| `10_delivery.md` | Delivery package and revisions | Phase 7 |

A playbook is written only once its commands exist and are tested, so it never refers to tools that do not exist.
