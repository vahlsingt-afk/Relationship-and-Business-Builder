---
id: 2026-05-07-patrick-nelson-discovery-call
person: patrick-nelson
date: 2026-05-07
channel: video
direction: bidirectional
substance: high
duration_min: 29
artifact: Fathom recording — https://fathom.video/share/Zs9tfQ_5Nxozn1-x2B-goo9W6EtbH28L
source_file: User-pasted transcript ingested 2026-05-08
---

## Summary
29-minute discovery call. Patrick attended the Hospitality Table chapter of SCN as a guest (introduced via Allison Simmons → Noelle Labrie → Patrick). Initial mutual exploration. Substantive content shared in both directions: Patrick walked through his platform architecture and current niche-finding mode; Todd explained the enterprise restaurant tech landscape (Oracle / NCR / PAR / Toast / Square dynamics) and the franchisor-franchisee tech-buying complexity. Both expressed mutual interest in continuing.

## Key context on Patrick
- **Background**: 25 years software development. 17 years corporate (Enterprise Architect at Chevron — supply chain, upstream, downstream). Burned out, started his own business during COVID. Was in Houston; refused return-to-office, kept the company going.
- **Business arc**: Started with a martial arts franchise as his first big client (95+ locations, still ongoing). Expanded to youth sports, automotive tinting/glass, Houston-area municipal tax collection, PEO companies, jewelry, manufacturing, shooting clubs.
- **Strategic shift ~12 months ago**: Moved from per-client custom builds to a unified platform with feature-flag toggles. Modules include CRM, billing, POS, inventory, communications (email/text), job/field service, route scheduling, subscriptions, memberships, events.
- **Architecture**: Built on inheritance — parent (e.g., franchisor) defines product catalog, locations inherit, can extend with one-offs if franchisor permits. Franchisor controls what franchisees can add. Reports roll up to common product IDs.
- **AI strategy**: Explicitly anti-hallucination. Built data foundation first, AI MCP layer on top. Aligned with Todd's "AI is a tool, not magic" thesis — Patrick named it the same way (*"behind the scenes... it's not connected... it's going to be messy"*).
- **Revenue model**: Has a payments-provider relationship that gives him revenue share. Subsidizes lower subscription pricing.
- **Current state**: Self-described "experimentation mode." Looking for niche patterns where his platform fits, before locking marketing to a target segment. Multi-location and franchise scale are his sweet spot.

## RI signals from this call

### Strong-positive signals
- **Mutual personal connection moment** — Patrick collects shot glasses (memory markers per place); Todd collects hand-carved animals on the same principle. Spontaneous "same idea" exchange. Real human rapport, not transactional rapport.
- **Domain-philosophy alignment** — Patrick's "data foundation first, AI on top" architecture matches Todd's stated thesis. Patrick reached the same conclusion independently. That's a peer-level signal.
- **Franchisor-franchisee tech complexity** — Patrick recognized this as the real challenge during the call ("you got 100 plus, everybody had a different POS"); his martial arts franchise built standardization across 95+ locations. He understands the structural problem, not just the surface tech problem.
- **Patrick explicitly asked Todd's domain perspective** — *"people like yourself who are in the industry, where are the gaps and the pain points?"* — that's not a vendor pitch, that's positioning Todd as the operator-knowledge half of a complementary pair.

### Friction / open questions
- Patrick is **outside the restaurant industry** — Todd had to coach him on Toast / Square / Oracle / NCR / PAR / payments dynamics during the call. He's smart and connects fast, but the domain knowledge gap is real.
- Patrick's natural niches lean toward POS-plus-other-modules (shooting clubs, jewelry, custom services). Pure-restaurant is a harder fit for him head-to-head against Toast.
- Movie theater example was a soft attempt to find adjacency; Patrick wasn't sure if those systems integrate (they generally don't, FWIW — separate concession + ticketing stacks).

### Strategic implications
The relationship shape that emerged: **Patrick brings a flexible platform; Todd brings restaurant-industry direction.** If a BridgePoint Ops client surfaces with a pain point that fits Patrick's stack (pre-CTO restaurant chain or restaurant-adjacent operator with multi-location complexity beyond pure POS), the referral is potentially viable. *Per the BridgePoint Ops engagement boundaries, this is "Pure Referral — rare, controlled, only when Todd has high conviction in product, fit, and deployment capability."* Conviction not yet established — Patrick has the architecture story but no restaurant-industry deployment track record yet.

## Connection chain
**Allison Simmons** → introduced Patrick to **Noelle Labrie** (Hospitality Table chapter president) → Patrick attended chapter as guest → met Todd. Allison facilitated the lead-in. Worth noting in her record.

## Loops opened
- **Schedule follow-up call with Patrick next week** (May 12-16 window). Patrick wants a specific example scenario to dig into. (Loop L-2026-05-08-019)
- **Todd to prep an example scenario** before the next call: a specific franchise/restaurant pain point with workflow specifics that would let Patrick demonstrate his platform's fit (or lack of fit). (Loop L-2026-05-08-020)

## Loops closed
- **L-2026-05-08-004** (Send concise reply about multi-location restaurant standardization) — superseded. The call itself addressed this question, with Todd walking Patrick through the standardization-in-franchising dynamics in real time.

## Signal implication
Patrick's signal class promotes from **LMI → LKI**. Evidence: substantive 29-min bidirectional video call, mutual personal-rapport exchange, domain-philosophy alignment, explicit follow-up commitment from both sides. This is solid LKI, not LMI.

## Style note for next interaction
Patrick is engineering-trained, asks direct questions, appreciates concrete examples over abstract framings. The example-scenario prep should be a *real* operator pain point with named workflow steps, not a hypothetical — that's how Patrick processes information.
