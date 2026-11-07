# 02 Transcript review

## Purpose
Make the words trustworthy before cutting and captioning: correct names and terms, resolve
doubtful words, and flag sensitive content for the human.

## Inputs
`analysis/<src>/transcript.txt` (doubtful words marked ⟨?word?⟩), `words.json`, the client glossary
(`styles/clients/<client>.glossary.yaml`, if it exists), the brief.

## Procedure
1. Read the whole transcript once, start to end, as a viewer would. Note the story.
2. **Names, brands, jargon, numbers.** List every proper noun and number. Compare with the brief and glossary.
   Typical Whisper errors: homophones ("Odess" vs "Odessa"), split numbers ("10 ,000"), hyphenated
   words split ("saber -toothed"), brand capitalisation.
3. **Doubtful words** (⟨?…?⟩): decide from context. If a doubtful word is a name, number or claim and
   context does not settle it, list it as a question for the human. Look at frames if on-screen text may show it
   (`cutroom frames <job> --source S01 --at <t>`).
4. **Re-transcribe with hints when names are wrong throughout:** `cutroom transcribe <job> --force --hotword "Dan Odess" --hotword "Tularosa"`,
   then `cutroom analyze <job> --force` to rebuild words. Record the glossary terms in the client glossary file for next time.
5. **Sensitive content.** Flag (do not cut on your own): medical, financial or legal advice; claims about
   identifiable people or companies; private data (addresses, phone numbers, faces of bystanders who did not consent);
   profanity (check the client's policy); off-the-record remarks; factual self-corrections.
6. **Off-mic voices.** Interviewer questions and chatter: note them for the cut plan. Interviewer
   questions are usually cut when the answer stands on its own.

## Decision rules
- You may fix spelling/capitalisation in captions later; you may **not** change what was said.
- A factual self-correction ("10,000 years, 8,000 years") is always a human decision.

## Outputs
`jobs/<id>/transcript_notes.md`: corrections, glossary terms, open questions, sensitive-content flags with timestamps.

## Failure handling
- Transcript badly wrong (accent, noise): `cutroom transcribe <job> --force --model large-v3-turbo`; if still bad, tell the human.

## Checklist
- [ ] Every proper noun and number checked
- [ ] Doubtful words resolved or listed
- [ ] Sensitive content flagged with timestamps
- [ ] Glossary updated
