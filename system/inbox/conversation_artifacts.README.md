# Conversation Artifacts Inbox

Drop exported meeting artifacts here for local processing:

- `system/inbox/conversation_artifacts/fathom/`
- `system/inbox/conversation_artifacts/zoom/`
- `system/inbox/conversation_artifacts/otter/`
- `system/inbox/conversation_artifacts/fireflies/`
- `system/inbox/conversation_artifacts/teams/`
- `system/inbox/conversation_artifacts/manual/`

Raw transcripts, chat logs, and notetaker exports are git-ignored. Canonical relationship evidence belongs in generated Interaction Briefs under `system/briefs/`, with source provenance.

## Todd's active watch setup

The current local watch configuration lives at:

```text
system/source_watch.yaml
```

Initial watched sources:

- Zoom native saves: `/Users/toddvahlsing/Documents/Zoom`
- RB Zoom drop folder: `system/inbox/conversation_artifacts/zoom/`
- Fathom downloads: `/Users/toddvahlsing/Downloads`
- RB Fathom drop folder: `system/inbox/conversation_artifacts/fathom/`

Downloads is noisy, so the Fathom Downloads watch requires Fathom-like filename hints such as `fathom`, `transcript-`, or `recording-`, and excludes generic RB/ChatGPT session transcript exports.

To inventory watched files:

```bash
python3 system/scripts/source_watch.py --cache --json
```

The scanner writes:

```text
system/.cache/source_watch.json
```

This is an inventory/freshness layer only. It does not yet parse transcripts, create briefs, update contacts, or open/close loops. Those mutations remain review-first under P-020.
