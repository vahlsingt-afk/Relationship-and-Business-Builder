# Relationship & Business Builder (RB) — System

Your Chief of Staff for the network you actually have.

## The premise

You have thousands of LinkedIn (and other) connections. You personally know maybe 10% of them. You have a real, working relationship with maybe 5%. The rest is dormant capacity — people who would help if asked, intros that would land if pursued, referral circles that would activate if convened.

This system bridges that gap.

## How it works in one paragraph

The system maintains a small set of canonical files about your network — a baseline index of every contact, Relationship Cards for the people you actually know, Interaction Briefs as evidence of contact, Circles to activate the network around specific goals, and a list of high-leverage intro brokers. LinkedIn exports, email, calendar, CRM, and conversation with you are *inputs* that update those files. Every day, the system regenerates `today.md` — "what is important today" — based on dormancy, open loops, newly-promoted relationships, and Circle activation state.

## What's in this folder

- `ARCHITECTURE.md` — how the system thinks. Read this first.
- `SCHEMAS.md` — the data model. Read this if you're touching files.
- `heuristics.md` — cluster-level network rules consulted by the intro engine (e.g., "anyone in SCN already knows Donnie"). Read at session start.
- `baseline_index.json` — every contact, with current signal class. Canonical.
- `cards/` — Relationship Cards (RCs) for recognized relationships.
- `briefs/` — Interaction Briefs (IBs), append-only evidence atoms.
- `circles/` — your active Circles (goal-anchored network activations).
- `intro_brokers.md` — derived list of high-leverage connectors in your network, by domain.
- `loop_ledger.md` — open loops awaiting deliberate closure.
- `today.md` — regenerated daily: what's important today.

## What's *not* in this folder

The legacy GPT build (24 governance PDFs and one docx, dated 8.2.x through 8.5) sits one level up in the parent folder. It is not used by this system. It contains useful domain vocabulary (the VC → NPR → LMI → LKI → RC signal hierarchy) which has been preserved here, and the rest is sunset.

## Day one workflow

1. Drop your LinkedIn data archive (`.zip`) into the folder. The system extracts `Connections.csv` and seeds `baseline_index.json` with everyone as a VC (Virtual Connection).
2. Tell the system about the people you actually know — these become RCs at appropriate tiers.
3. The 5 starting Circles in `circles/` are pre-stubbed. Tell the system who belongs in each.
4. Ask the system: "What's important today?" — it generates `today.md`.

## Day-two and beyond

Connect email and calendar so the system can derive Interaction Briefs automatically and promote relationships up the signal hierarchy without manual entry. Capture relationship shifts in real time ("just had coffee with Jane, she's looking for senior PMs"). The longer it runs, the more valuable it gets — but the baseline value comes on day one.
