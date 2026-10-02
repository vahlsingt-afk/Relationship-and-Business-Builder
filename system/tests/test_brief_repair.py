"""
test_brief_repair.py — RB-DEFECT-072.

brief_repair.py's job: given rendered markdown that trips
brief_acceptance_check.check_no_duplicate_story_clusters, deterministically
remove all but the most complete entry per same-event cluster, and leave
the gate passing afterward. These tests pin exactly that contract, plus the
real 2026-09-23 incident case as a permanent regression.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brief_acceptance_check as bac  # noqa: E402
import brief_repair as br  # noqa: E402


def test_no_duplicates_is_a_no_op():
    md = (
        "## A: World Headlines\n\n"
        "[Story One](https://example.com/1)\n"
        "Source | 2026-09-23\n"
        "*Summary one.*\n"
        "**Why it matters:** Reason one.\n"
        "**[Read more →](https://example.com/1)**\n\n"
        "[Story Two](https://example.com/2)\n"
        "Source | 2026-09-23\n"
        "*Summary two.*\n"
        "**Why it matters:** Reason two.\n"
        "**[Read more →](https://example.com/2)**\n"
    )
    out = br.repair_duplicate_story_clusters(md)
    assert out["changed"] is False
    assert out["removed"] == []
    assert out["repaired_markdown"] == md


def test_duplicate_removal_keeps_the_more_complete_entry_and_passes_gate():
    """A same-event pair, one with the full Why-it-matters/Read-more block,
    one bare -- the fuller entry must survive."""
    md = (
        "## C: Restaurant Industry\n\n"
        "[Wingstop launches Game Day Punch Card](https://nrn.example/1)\n"
        "Nation's Restaurant News | 2026-09-22\n"
        "*The new loyalty promotion follows the chain's limited Wing Pass.*\n"
        "**Why it matters:** Drives traffic during football season.\n"
        "**[Read more →](https://nrn.example/1)**\n\n"
        "## D+: Curated Trade Reads\n\n"
        "### [Wingstop Unveils Game Day Punch Card for Rewards Members](https://trade.example/2)\n\n"
        "*QSR AM Jolt · Sep 22, 2026*\n\n"
        "**Why it matters:** Highlights loyalty focus.\n\n"
        "## J: Proof Dashboard\n\nDashboard content.\n"
    )
    assert bac.check_no_duplicate_story_clusters(md)["passed"] is False

    out = br.repair_duplicate_story_clusters(md)
    assert out["changed"] is True
    assert len(out["removed"]) == 1
    assert out["removed"][0]["url"] == "https://trade.example/2"
    assert out["kept"][0]["url"] == "https://nrn.example/1"

    assert bac.check_no_duplicate_story_clusters(out["repaired_markdown"])["passed"] is True
    # The surviving section's other real content must be untouched.
    assert "## J: Proof Dashboard" in out["repaired_markdown"]
    assert "Dashboard content." in out["repaired_markdown"]


def test_duplicate_removal_backfills_section_without_new_duplicate():
    """Removing a loser must not leave orphaned text from an adjacent entry,
    and must not introduce a new duplicate/gate failure of its own -- the
    remaining entries in the section render exactly as if the loser were
    never there. ("Backfill" here is defined as: the section falls back to
    its remaining real entries, never a fabricated replacement.)"""
    md = (
        "## C: Restaurant Industry\n\n"
        "[Acme Burger launches Fall Loyalty Card promotion](https://a.example/1)\n"
        "Source | 2026-09-22\n"
        "**Why it matters:** Reason A.\n"
        "**[Read more →](https://a.example/1)**\n\n"
        "[Acme Burger unveils Fall Loyalty Card for members](https://a.example/2)\n"
        "Source | 2026-09-22\n"
        "**Why it matters:** Reason A restated.\n"
        "**[Read more →](https://a.example/2)**\n\n"
        "[Unrelated Story B](https://b.example/1)\n"
        "Source | 2026-09-22\n"
        "**Why it matters:** Reason B.\n"
        "**[Read more →](https://b.example/1)**\n\n"
        "*3 items this cycle.*\n\n---\n"
    )
    out = br.repair_duplicate_story_clusters(md)
    assert out["changed"] is True
    repaired = out["repaired_markdown"]
    # The unrelated, non-duplicate story is fully intact -- not swallowed by
    # the neighboring removal.
    assert "[Unrelated Story B](https://b.example/1)" in repaired
    assert "**Why it matters:** Reason B." in repaired
    assert "**[Read more →](https://b.example/1)**" in repaired
    assert bac.check_no_duplicate_story_clusters(repaired)["passed"] is True


def test_three_way_cluster_keeps_exactly_one():
    md = (
        "## A: World Headlines\n\n"
        "[Russia hits Ukrainian train near Poland](https://a.example/1)\n\n"
        "[Russian drone hits train near Ukraine-Poland border](https://a.example/2)\n\n"
        "[Russian strike hits train close to Poland border](https://a.example/3)\n\n"
        "## B: National Headlines\n\nstuff\n"
    )
    out = br.repair_duplicate_story_clusters(md)
    assert out["changed"] is True
    assert len(out["removed"]) == 2
    assert bac.check_no_duplicate_story_clusters(out["repaired_markdown"])["passed"] is True
    assert "## B: National Headlines" in out["repaired_markdown"]


def test_repair_never_touches_bold_or_numbered_callouts():
    """The gate's own _TITLE_LINK_RE deliberately does not match a bold
    "**[title](url)**" Top Story callout or a "N. **[title](url)**" numbered
    entry -- repair must respect the exact same boundary, never surgically
    remove content the gate itself doesn't consider a headline entry."""
    md = (
        "## Top Story\n\n"
        "**[Presto integrates with Toast to scale drive-thru voice AI](https://a.example/top)** — commentary.\n\n"
        "## K: GP/Genius — Field Intelligence\n\n"
        "[Presto integrates with Toast to scale drive-thru voice AI](https://a.example/k1)\n"
        "Restaurant Dive\n\n"
        "[Toast aims to scale drive-thru AI](https://a.example/k2)\n"
        "Payments Dive\n\n---\n"
    )
    out = br.repair_duplicate_story_clusters(md)
    assert out["changed"] is True
    # The Top Story bold callout survives untouched even though it names
    # the same event -- it was never a member of the cluster to begin with.
    assert "**[Presto integrates with Toast to scale drive-thru voice AI](https://a.example/top)** — commentary." in out["repaired_markdown"]
    assert len(out["removed"]) == 1


def test_real_2026_09_23_incident_is_fixed_and_stays_fixed():
    """Permanent regression pin: the exact real headline pairs from the
    2026-09-23 incident that RB-DEFECT-072 was filed over."""
    md = (
        "## C: Restaurant Industry\n\n"
        "[Wingstop launches Game Day Punch Card, its second football promotion of the season](https://www.nrn.example/wingstop)\n"
        "Nation's Restaurant News | 2026-09-22\n"
        "*The new loyalty promotion follows the chain's limited Wing Pass, giving Club Wingstop members a free Watch Party Bundle after three purchases.*\n"
        "**Why it matters:** Wingstop's new Game Day Punch Card promotion is designed to drive customer traffic during the football season.\n"
        "**[Read more →](https://www.nrn.example/wingstop)**\n\n"
        "*4 items this cycle.*\n\n---\n\n"
        "## D+: Curated Trade Reads\n\n"
        "### [Wingstop Unveils ‘Game Day Punch Card’ for Rewards Members](https://click.example/wingstop2)\n\n"
        "*QSR AM Jolt · Sep 22, 2026*\n\n"
        "**Why it matters:** Wingstop's introduction of a 'Game Day Punch Card' for rewards members highlights their focus on enhancing customer engagement.\n\n"
        "## K: GP/Genius — Field Intelligence\n\n"
        "[Presto integrates with Toast to scale drive-thru voice AI](https://link.example/presto)\n"
        "Restaurant Dive\n\n"
        "[Toast aims to scale drive-thru AI](https://link.example/toast)\n"
        "Payments Dive\n\n---\n"
    )
    assert bac.check_no_duplicate_story_clusters(md)["passed"] is False
    out = br.repair_duplicate_story_clusters(md)
    assert out["changed"] is True
    kept_titles = {k["title"] for k in out["kept"]}
    assert "Wingstop launches Game Day Punch Card, its second football promotion of the season" in kept_titles
    assert "Presto integrates with Toast to scale drive-thru voice AI" in kept_titles
    result = bac.check_no_duplicate_story_clusters(out["repaired_markdown"])
    assert result["passed"] is True
