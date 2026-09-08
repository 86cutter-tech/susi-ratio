#!/usr/bin/env python3
"""
2027학년도 수시 실시간 경쟁률 수집기
- config/sources.json 의 대학별 소스(입학처 / 진학어플라이 / 유웨이어플라이)를 순회
- HTML 표에서 (전형, 모집단위, 모집인원, 지원인원, 경쟁률) 열을 자동 인식
- 복수 소스 교차 검증 → docs/data/latest.json / history.json / prev_year.json 갱신

실행: python scraper/scrape.py            (전체)
      python scraper/scrape.py --only 한국외대   (특정 대학만, 디버깅용)
      python scraper/scrape.py --dump 한국외대   (원본 HTML을 debug/ 에 저장)
"""
import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config"
OUT = ROOT / "docs" / "data"
DEBUG = ROOT / "debug"
KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# 열 인식 키워드 (헤더 텍스트에 포함되면 해당 열로 판정)
COL_KEYS = {
    "track": ["전형명", "전형유형", "전형구분", "전형"],
    "unit": ["모집단위", "학과", "학부", "전공", "모집학과", "학과(부)"],
    "quota": ["모집인원", "모집정원", "정원"],
    "applicants": ["지원인원", "지원자수", "지원자", "접수인원", "지원"],
    "rate": ["경쟁률", "경쟁율"],
}
HISTORY_MAX_POINTS = 400  # 모집단위당 보관 스냅샷 수


def now_kst():
    return datetime.now(KST)


def clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s


def to_int(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


def to_rate(s):
    """'3.25 : 1', '3.25:1', '3.25' → 3.25"""
    m = re.search(r"(\d+(?:\.\d+)?)", s or "")
    return float(m.group(1)) if m else None


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    # 국내 대학 사이트는 euc-kr / cp949가 섞여 있어 apparent_encoding으로 보정
    if r.encoding is None or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    return r.text


def classify_header(cells):
    """헤더 셀 리스트 → {열index: 역할}. 역할이 3개(unit, applicants 또는 rate 포함) 미만이면 None."""
    roles = {}
    for i, c in enumerate(cells):
        t = clean(c)
        for role, keys in COL_KEYS.items():
            if role in roles.values():
                continue
            if any(k in t for k in keys):
                # '전형' 키워드가 '모집단위'와 겹치지 않도록 우선순위 처리
                if role == "track" and any(k in t for k in COL_KEYS["unit"]):
                    continue
                roles[i] = role
                break
    have = set(roles.values())
    if "unit" in have and ("applicants" in have or "rate" in have):
        return roles
    return None


def expand_table(table):
    """rowspan/colspan을 평면화한 2차원 텍스트 배열 반환."""
    grid = []
    spans = {}  # (row, col) -> text (rowspan 이월용)
    rows = table.find_all("tr")
    for ri, tr in enumerate(rows):
        row = []
        ci = 0
        cells = tr.find_all(["th", "td"])
        cell_iter = iter(cells)
        # 이월된 셀 채우기
        while True:
            while (ri, ci) in spans:
                row.append(spans.pop((ri, ci)))
                ci += 1
            cell = next(cell_iter, None)
            if cell is None:
                break
            text = clean(cell.get_text(" "))
            rs = int(cell.get("rowspan", 1) or 1)
            cs = int(cell.get("colspan", 1) or 1)
            for k in range(cs):
                row.append(text)
                for r in range(1, rs):
                    spans[(ri + r, ci)] = text
                ci += 1
        # 남은 이월 셀
        while (ri, ci) in spans:
            row.append(spans.pop((ri, ci)))
            ci += 1
        if row:
            grid.append(row)
    return grid


def parse_tables(html: str):
    """페이지 내 모든 표에서 경쟁률 행을 추출."""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for table in soup.find_all("table"):
        grid = expand_table(table)
        if len(grid) < 2:
            continue
        # 헤더는 첫 1~3행 중 열 인식이 되는 행
        roles, header_idx = None, None
        for hi in range(min(3, len(grid))):
            roles = classify_header(grid[hi])
            if roles:
                header_idx = hi
                break
        if not roles:
            continue
        last_track = ""
        for row in grid[header_idx + 1:]:
            get = lambda role: next((row[i] for i, r in roles.items() if r == role and i < len(row)), "")
            unit = clean(get("unit"))
            if not unit or unit in ("합계", "총계", "계", "소계"):
                continue
            track = clean(get("track")) or last_track
            last_track = track
            rec = {
                "track": track,
                "unit": unit,
                "quota": to_int(get("quota")),
                "applicants": to_int(get("applicants")),
                "rate": to_rate(get("rate")),
            }
            if rec["rate"] is None and rec["quota"] and rec["applicants"] is not None:
                rec["rate"] = round(rec["applicants"] / rec["quota"], 2)
            if rec["rate"] is None and rec["applicants"] is None:
                continue
            records.append(rec)
    return records


def extract_update_time(html: str):
    """페이지 내 '2026-09-08 16:30 기준' 류 문자열에서 대학측 갱신 시각 추출 (없으면 None)."""
    m = re.search(r"(20\d{2})[.\-/년 ]+(\d{1,2})[.\-/월 ]+(\d{1,2})[일 ]*[^\d]{0,12}(\d{1,2})[:시 ]+(\d{2})", html)
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    try:
        return datetime(y, mo, d, h, mi, tzinfo=KST).isoformat()
    except ValueError:
        return None


def key_of(univ, rec):
    return f"{univ}|{rec['track']}|{rec['unit']}"


def cross_validate(univ, per_source):
    """소스별 레코드 → 키별 통합. 지원인원이 다르면 큰 값(더 최신) 채택 + 불일치 표시."""
    merged = {}
    for src_type, recs in per_source.items():
        for rec in recs:
            k = key_of(univ, rec)
            entry = merged.setdefault(k, {"univ": univ, **rec, "sources": {}, "mismatch": False})
            entry["sources"][src_type] = {"applicants": rec["applicants"], "rate": rec["rate"]}
            if rec["quota"] and not entry.get("quota"):
                entry["quota"] = rec["quota"]
            a_new, a_old = rec["applicants"], entry["applicants"]
            if a_new is not None and a_old is not None and a_new != a_old:
                entry["mismatch"] = True
                if a_new > a_old:
                    entry["applicants"], entry["rate"] = a_new, rec["rate"]
            elif a_old is None and a_new is not None:
                entry["applicants"], entry["rate"] = a_new, rec["rate"]
    return merged


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def build_prev_year():
    p = CFG / "prev_year.csv"
    out = {}
    if not p.exists():
        return out
    with p.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            k = f"{clean(row['대학'])}|{clean(row['전형'])}|{clean(row['모집단위'])}"
            out[k] = {"quota": to_int(row.get("모집인원")), "applicants": to_int(row.get("지원인원")), "rate": to_rate(row.get("경쟁률"))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="특정 대학만 수집")
    ap.add_argument("--dump", help="특정 대학 원본 HTML을 debug/에 저장")
    args = ap.parse_args()

    cfg = load_json(CFG / "sources.json", {"universities": []})
    OUT.mkdir(parents=True, exist_ok=True)
    latest_prev = load_json(OUT / "latest.json", {"items": {}})
    history = load_json(OUT / "history.json", {})
    ts = now_kst().replace(second=0, microsecond=0).isoformat()

    all_items = dict(latest_prev.get("items", {}))  # 실패한 대학은 직전 값을 유지
    status = {}

    for u in cfg["universities"]:
        name = u["name"]
        if args.only and name != args.only:
            continue
        per_source, errors, page_time = {}, [], None
        for s in u.get("sources", []):
            url = (s.get("url") or "").strip()
            if not url or url.upper() == "TODO":
                continue
            try:
                html = fetch(url)
                if args.dump == name:
                    DEBUG.mkdir(exist_ok=True)
                    (DEBUG / f"{name}_{s['type']}.html").write_text(html, encoding="utf-8")
                recs = parse_tables(html)
                if not recs:
                    errors.append(f"{s['type']}: 표 인식 실패(0행) — iframe/JS 렌더링 페이지일 수 있음")
                    continue
                per_source[s["type"]] = recs
                page_time = page_time or extract_update_time(html)
                print(f"[{name}] {s['type']}: {len(recs)}행", file=sys.stderr)
            except Exception as e:  # noqa
                errors.append(f"{s['type']}: {type(e).__name__}: {e}")
            time.sleep(0.8)  # 서버 부하 배려

        merged = cross_validate(name, per_source)
        for k, v in merged.items():
            v["fetched_at"] = ts
            v["page_time"] = page_time
            all_items[k] = v
            pts = history.setdefault(k, [])
            if not pts or pts[-1][1] != v["applicants"]:
                pts.append([ts, v["applicants"], v["rate"]])
                if len(pts) > HISTORY_MAX_POINTS:
                    del pts[: len(pts) - HISTORY_MAX_POINTS]
        status[name] = {
            "ok": bool(merged),
            "rows": len(merged),
            "sources": list(per_source.keys()),
            "errors": errors,
            "fetched_at": ts if merged else latest_prev.get("status", {}).get(name, {}).get("fetched_at"),
        }
        if not merged:
            print(f"[{name}] 수집 실패: {errors or '소스 URL 미설정'}", file=sys.stderr)

    (OUT / "latest.json").write_text(
        json.dumps({"generated_at": ts, "status": status, "items": all_items}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (OUT / "history.json").write_text(json.dumps(history, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "prev_year.json").write_text(json.dumps(build_prev_year(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "humanities.json").write_text((CFG / "humanities.json").read_text(encoding="utf-8"), encoding="utf-8")
    ok = sum(1 for s in status.values() if s["ok"])
    print(f"완료: {ok}/{len(status)}개 대학, 총 {len(all_items)}개 모집단위, {ts}", file=sys.stderr)


if __name__ == "__main__":
    main()
