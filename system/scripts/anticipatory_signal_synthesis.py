#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from collections import defaultdict
from datetime import date,datetime,timezone
import rb_core as core
RESULT_PATH=core.CACHE_DIR/"early_intelligence_signals.json"
INPUTS=[core.CACHE_DIR/"entity_page_changes.json",core.CACHE_DIR/"sitemap_signal_candidates.json",core.CACHE_DIR/"fdd_release_candidates.json",core.CACHE_DIR/"public_artifact_candidates.json",core.CACHE_DIR/"distress_filing_candidates.json"]
HORIZON={"company_job_boards":"weeks_to_months","domain_certificate_activity":"days_to_weeks","trademarks":"weeks_to_months","development_and_permits":"months","pricing_and_terms":"days_to_weeks","developer_ecosystem":"days_to_weeks","concession_procurement":"weeks_to_months","vendor_status":"immediate"}
def _load(p):
    try:return json.loads(p.read_text())
    except (OSError,ValueError):return {}
def run(*,today:date|None=None)->dict:
    today=today or date.today(); raw=[]
    pages=_load(INPUTS[0])
    for r in pages.get("changes") or []: raw.append({"entity":r.get("entity"),"signal_type":r.get("source_category") or r.get("page_type"),"source_url":r.get("url"),"observed_at":r.get("detected_at"),"evidence":r.get("change_excerpt"),"official":r.get("evidence_tier")=="official"})
    for r in _load(INPUTS[1]).get("new_candidates") or []: raw.append({"entity":r.get("entity"),"signal_type":"new_public_artifact","source_url":r.get("url"),"observed_at":r.get("detected_at"),"evidence":"New material URL appeared in first-party sitemap","official":False})
    for r in _load(INPUTS[2]).get("new_candidates") or []: raw.append({"entity":r.get("brand"),"signal_type":"fdd_release","source_url":r.get("source_url"),"observed_at":r.get("detected_at"),"evidence":f"New {r.get('document_type')} dated {r.get('fdd_date')}","official":False})
    for r in _load(INPUTS[3]).get("new_candidates") or []: raw.append({"entity":r.get("entity"),"signal_type":r.get("source_category") or "public_artifact","source_url":r.get("url"),"observed_at":r.get("detected_at"),"evidence":r.get("title"),"official":r.get("source_category")=="procurement_and_board_packets"})
    for r in _load(INPUTS[4]).get("new_candidates") or []: raw.append({"entity":r.get("entity"),"signal_type":r.get("source_type") or "distress_filing","source_url":r.get("source_url"),"observed_at":r.get("detected_at"),"evidence":r.get("excerpt"),"official":True})
    groups=defaultdict(list)
    for r in raw:
        if r.get("entity"):groups[r["entity"]].append(r)
    signals=[]
    for entity,items in groups.items():
        types=sorted({i["signal_type"] for i in items}); confidence="high" if len(types)>=2 and any(i["official"] for i in items) else "medium" if len(types)>=2 else "low"
        signals.append({"entity":entity,"signal_types":types,"observed_at":max((i.get("observed_at") or "") for i in items),"expected_horizon":HORIZON.get(types[0],"days_to_weeks"),"confidence":confidence,"evidence_chain":[{"url":i.get("source_url"),"observation":i.get("evidence")} for i in items[:5]],"what_confirms":"A second independent source, named deployment, filing, contract, or company statement.","what_disproves":"The changed artifact is reverted, administrative noise, or unrelated to a buying or competitive event.","lead_time_days":None,"status":"pending_review"})
    signals.sort(key=lambda x:({"high":0,"medium":1,"low":2}[x["confidence"]],x["entity"])); result={"contract":"rb_early_intelligence_signals_v1","date":today.isoformat(),"signals":signals,"policy":"hypothesis-first; no canonical mutation"}
    RESULT_PATH.write_text(json.dumps(result,indent=2)+"\n");return result
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--date");a=p.parse_args();print(json.dumps(run(today=date.fromisoformat(a.date) if a.date else None),indent=2))
