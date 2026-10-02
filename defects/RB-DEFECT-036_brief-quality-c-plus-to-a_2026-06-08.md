# RB-DEFECT-036 — Brief Quality C+ → A Sprint

**Filed:** 2026-06-08  
**Severity:** High — daily brief scoring C+ (68/100) against world-class CoS standard  
**Status:** Resolved (all six gaps addressed) — 2026-06-08  
**Assessment source:** Todd's explicit C+ grade with six scored subscores

---

## The Six Gaps and Their Fixes

### 1. No sourced news headlines (was 2/10)

**Root cause:** `_build_morning_headlines` read from `market_signals_report.get("top")`
which was stale. Web scanner had 160 fresh articles with full URLs, but `why_it_matters`
field contained junk template text ("product launch signal via BBC Business") that
the GPT latched onto instead of the real content.

**Fixes:**
- `web_scanner.py` — `why_it_matters` now = actual article description (up to 300
  chars). For high-signal event types (acquisition, exec-change, closure, funding,
  earnings, labor) a one-line CoS context note is appended after the description.
- `daily_brief.py` — `_morning_headlines_section_items` fallback to web_scan buckets
  was added in DEFECT-035; the `why_it_matters` improvement means fallback items now
  carry real content, not template strings.
- `custom_gpt_instructions_compact_8k.md` — news sections must render as
  `[Source] — [Title](extras.source_url) → [summary]`. URL is mandatory.
- `CANONICAL_RESPONSE_CONTRACT.md` — added banned pattern: converting headlines to
  commentary. `extras.source_url` must appear as a link.
- `custom_gpt_operational_playbook.md` — added to Section 13 known failure patterns.

### 2. No sources on claims (was 1/10)

**Root cause:** GPT instructions had no explicit requirement for source citations on
factual claims about companies, opportunities, or market events.

**Fix:** `CANONICAL_RESPONSE_CONTRACT.md` added banned pattern: making a factual
claim without `(Source: name, date)` or a URL. `custom_gpt_instructions_compact_8k.md`
added Non-Negotiable: "Sources on every claim."

### 3. No signal inventory (was 3/10)

**Root cause:** Signals existed but were scattered across sections and blended into
narrative prose. No ranked list.

**Fix:** New `signal_inventory` section in `canonical_brief.sections`:
- `_signal_inventory_items(report, limit=12)` in `daily_brief.py`
- Aggregates: relationship signals, high-signal web-scan items (acquisition/exec-change/
  closure/funding/earnings), loop triggers (overdue/due-today), overnight mutation count
- Ranks by importance (high/medium/low) then confidence_pct
- Surfaces `importance`, `confidence_pct`, `signal_type`, `source`, `url` per signal
- Added to section_order between `weekly_plan_focus` and `resource_verification`
- GPT instructions mandate rendering as numbered ranked list: `Signal N [IMPORTANCE]
  — [title] | [source] | Confidence: X%`

### 4. No Monday-specific planning (was 2/10)

**Root cause:** Brief shape didn't change by day of week. Monday looked like Thursday.

**Fix:** 
- Monday auto-draft trigger (built earlier this session) surfaces weekly plan proposal.
- `personal_operating_system.yaml` defines `weekly_rhythm` per day — Monday mode is
  "Attack", Tuesday is "Execution", Friday is "Review + Restore", etc.
- `_personal_operating_system_items(today)` reads the day's mode, intent, and priority
  list and renders them in the brief.
- GPT instructions now call out Monday explicitly: surface weekly objectives and
  energy allocation prominently.

### 5. No Personal Operating System layer (was 3/10)

**Root cause:** RB had no life-domain concept. Brief only tracked career/opportunity.

**Fix:**
- New file: `system/personal_operating_system.yaml` — defines 5 life domains with
  allocation %s (Career Search 40%, Relationship Capital 20%, Thought Leadership 15%,
  Faith & Ministry 15%, Family 10%), current_focus, active_commitments, and weekly_goal
  per domain. Also defines weekly_rhythm (day-of-week modes) and operating_principles.
- `_personal_operating_system_items(today)` in `daily_brief.py` — renders today's
  operating mode + full energy allocation table. Loaded via YAML best-effort import.
- New section `personal_operating_system` in `canonical_brief.sections`, added to
  section_order after `signal_inventory`.
- GPT instructions mandate rendering as allocation table, not labels. Monday: surface
  weekly objectives for each domain explicitly.

### 6. No intelligence discovery (was 2/10)

**Root cause:** Brief restated known state. `signal_inventory` is the structural fix —
it forces high-signal items (acquisition, exec-change, funding) to the top of the
brief as named, ranked signals rather than buried in narrative.

**Partial fix:** `signal_inventory` surfaces `disposition: act_today` items first.
First test run shows Signal 1: `&pizza/Tijuana Flats CEO search` (exec-change, HIGH),
Signal 2: `&pizza acquisition` (acquisition, HIGH) — both were in the web scan data
but invisible in the previous brief. These are exactly the "things you didn't know"
the assessment called out.

**What's left:** Signal discovery from LinkedIn activity (job changes, profile views,
content) remains gated on the LinkedIn connector gap (known, DEFECT-034 #2). Within
the current source set, `signal_inventory` + improved `why_it_matters` closes the
visible gap.

---

## Files Changed

- `system/scripts/daily_brief.py` — `_signal_inventory_items`, `_personal_operating_system_items`, `_load_personal_operating_system`, new sections in dict + section_order
- `system/scripts/web_scanner.py` — `why_it_matters` = article description, high-signal CoS context notes
- `system/personal_operating_system.yaml` — NEW: life domains, weekly rhythm, operating principles
- `system/api/custom_gpt_instructions_compact_8k.md` — news link mandate, signal inventory format, POS section, sources-on-claims non-negotiable
- `system/api/CANONICAL_RESPONSE_CONTRACT.md` — 5 new banned patterns
- `system/api/custom_gpt_operational_playbook.md` — 5 new Section 13 entries
- `system/api/DAILY_BRIEF_CANONICAL_TEMPLATE.md` — 0e/0f sections documented

## GPT Re-upload Required

`custom_gpt_instructions_compact_8k.md` (4,473 chars — updated),
`CANONICAL_RESPONSE_CONTRACT.md`, `custom_gpt_operational_playbook.md`,
`DAILY_BRIEF_CANONICAL_TEMPLATE.md` all need re-upload for the GPT to
enforce the new rendering standards.
