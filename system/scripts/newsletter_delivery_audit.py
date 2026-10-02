#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from datetime import date, datetime, timezone
from pathlib import Path
import rb_core as core

CONFIG_PATH = core.INBOX_DIR / "newsletter_delivery_expectations.json"
REGISTRY_PATH = core.CACHE_DIR / "email_intelligence_sources.json"
RESULT_PATH = core.CACHE_DIR / "newsletter_delivery_audit.json"

def _load(path: Path) -> dict:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError): return {}

def _norm(v: str) -> str: return re.sub(r"[^a-z0-9]+", " ", (v or "").lower()).strip()

def run(*, today: date | None = None) -> dict:
    today = today or date.today(); observed = _load(REGISTRY_PATH).get("sources") or []
    rows=[]
    for expected in _load(CONFIG_PATH).get("newsletters") or []:
        aliases=[_norm(x) for x in expected.get("aliases") or [expected.get("publication") or expected.get("name") or ""]]
        hit=None
        for source in observed:
            hay=_norm(f"{source.get('publication','')} {source.get('sender','')}")
            if any(alias and alias in hay for alias in aliases): hit=source; break
        last=(hit or {}).get("last_received_at"); age=None
        if last:
            try: age=(today-datetime.fromisoformat(last.replace("Z","+00:00")).date()).days
            except ValueError: pass
        status=("not_seen" if not hit else "delivery_verified" if age is not None and age <= int(expected.get("cadence_days",7))*2 else "stale")
        rows.append({**expected,"status":status,"last_received_at":last,"age_days":age,
                     "full_body_capture_expected":True,"observed_sender":(hit or {}).get("sender")})
    result={"contract":"rb_newsletter_delivery_audit_v1","date":today.isoformat(),"newsletters":rows,
            "summary":{s:sum(r["status"]==s for r in rows) for s in ("delivery_verified","stale","not_seen")},
            "policy":"configured is not subscribed; only observed inbox delivery is verified"}
    RESULT_PATH.parent.mkdir(parents=True,exist_ok=True); RESULT_PATH.write_text(json.dumps(result,indent=2)+"\n")
    return result

if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--date"); a=p.parse_args()
    print(json.dumps(run(today=date.fromisoformat(a.date) if a.date else None),indent=2))
