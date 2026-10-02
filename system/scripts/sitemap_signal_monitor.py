#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, urllib.request, xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
import rb_core as core

CONFIG_PATH=core.INBOX_DIR/"sitemap_watchlist.json"; STATE_PATH=core.CACHE_DIR/"sitemap_signal_state.json"; RESULT_PATH=core.CACHE_DIR/"sitemap_signal_candidates.json"
MATERIAL_RE=re.compile(r"(customer|case-stud|partner|integration|product|release|payment|pos|loyalty|kiosk|ai|pricing|investor|leadership|security)",re.I)
def _load(p:Path)->dict:
    try:return json.loads(p.read_text())
    except (OSError,ValueError):return {}
def _fetch(url:str)->bytes:
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 RB-Sitemap-Monitor/1.0"})
    with urllib.request.urlopen(req,timeout=20) as r:return r.read(5_000_000)
def parse(raw:bytes)->tuple[str,list[str]]:
    root=ET.fromstring(raw); kind=root.tag.rsplit('}',1)[-1]
    return kind,[x.text.strip() for x in root.findall('.//{*}loc') if x.text]
def run(*,today:date|None=None,fetcher=_fetch)->dict:
    today=today or date.today(); prior=_load(STATE_PATH); old=prior.get("urls") or {}; current={}; candidates=[]; errors=[]; baselined=0
    for row in _load(CONFIG_PATH).get("sitemaps") or []:
        try:
            kind,urls=parse(fetcher(row["url"])); page_urls=[]
            if kind=="sitemapindex":
                for child in urls[:20]:
                    try: page_urls.extend(parse(fetcher(child))[1])
                    except Exception: continue
            else: page_urls=urls
            page_urls=sorted(set(page_urls)); current[row["url"]]=page_urls
            if row["url"] not in old: baselined+=1; continue
            for url in sorted(set(page_urls)-set(old[row["url"]])):
                if MATERIAL_RE.search(url): candidates.append({"entity":row["entity"],"url":url,"source_sitemap":row["url"],"status":"pending_review","detected_at":datetime.now(timezone.utc).isoformat(timespec="seconds")})
        except Exception as exc: errors.append({"entity":row["entity"],"url":row["url"],"error":str(exc)[:240]})
    result={"contract":"rb_sitemap_signal_candidates_v1","date":today.isoformat(),"sitemaps":len(current),"baselined":baselined,"new_candidates":candidates,"errors":errors,"policy":"new URLs are discovery signals, not canonical claims"}
    STATE_PATH.parent.mkdir(parents=True,exist_ok=True); STATE_PATH.write_text(json.dumps({"date":today.isoformat(),"urls":current},indent=2)+"\n"); RESULT_PATH.write_text(json.dumps(result,indent=2)+"\n"); return result
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--date");a=p.parse_args();print(json.dumps(run(today=date.fromisoformat(a.date) if a.date else None),indent=2))
