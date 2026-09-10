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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://apply.jinhakapply.com/SmartRatio",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-site",
    "Cache-Control": "no-cache",
}

# 열 인식 키워드 (헤더 텍스트에 포함되면 해당 열로 판정)
COL_KEYS = {
    "campus": ["캠퍼스"],
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


SESSION = requests.Session()


def fetch(url: str) -> str:
    h = dict(HEADERS)
    home = "https://apply.jinhakapply.com/SmartRatio"
    if "uway" in url:
        home = "https://www.uwayapply.com/"
    h["Referer"] = home
    r = SESSION.get(url, headers=h, timeout=25)
    if r.status_code == 403:
        # 일부 사이트는 첫 요청만 막음 — 접수 사이트 홈을 먼저 열어 쿠키를 받은 뒤 재시도
        try:
            SESSION.get(home, headers=h, timeout=15)
        except Exception:  # noqa
            pass
        r = SESSION.get(url, headers=h, timeout=25)
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
            if role in roles.values() and role != "unit":
                continue
            if any(k in t for k in keys):
                # '전형' 키워드가 '모집단위'와 겹치지 않도록 우선순위 처리
                if role == "track" and any(k in t for k in COL_KEYS["unit"]):
                    continue
                if role == "unit" and "unit" in roles.values():
                    # colspan 된 '모집단위' (계열/단과대 | 학과): 앞 열은 group
                    prev = next(k for k, v in roles.items() if v == "unit")
                    roles[prev] = "group"
                roles[i] = role
                break
    have = set(roles.values())
    if "unit" in have and ("applicants" in have or "rate" in have):
        return roles
    return None


def _span(v):
    """rowspan/colspan 값을 안전하게 정수화 (템플릿 조각 등 이상값은 1로)."""
    try:
        n = int(re.sub(r"[^\d]", "", str(v or "")) or 1)
        return max(1, min(n, 200))
    except ValueError:
        return 1


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
            rs = _span(cell.get("rowspan"))
            cs = _span(cell.get("colspan"))
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


def track_ok(track: str, cfg) -> bool:
    """config의 track_include / track_exclude 로 전형 필터."""
    inc, exc = cfg.get("track_include") or [], cfg.get("track_exclude") or []
    if any(k in track for k in exc):
        return False
    return (not inc) or (not track) or any(k in track for k in inc)


def campus_ok(campus: str, cfg) -> bool:
    """캠퍼스 열이 있을 때 campus_include(기본 ['서울'])에 해당하는 행만 통과."""
    if not campus:
        return True
    inc = cfg.get("campus_include") or ["서울"]
    return any(k in campus for k in inc)


def parse_tables(html: str, cfg=None):
    """페이지 내 모든 표에서 경쟁률 행을 추출."""
    cfg = cfg or {}
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
        heading = ""
        node = table.find_previous(string=re.compile(r"경쟁률\s*현황|지원\s*현황"))
        if node:
            holder = node.parent if node.parent and node.parent.name not in ("body", "html", "[document]") else None
            full = holder.get_text(" ") if holder else str(node)
            heading = clean(re.sub(r"(경쟁률|지원)\s*현황.*$", "", full))
            if heading in ("전형별", "전체", "모집단위별", "캠퍼스별"):
                heading = ""
        last_track = heading
        last_group = ""
        last_campus = ""
        ncol = len(grid[header_idx])
        for row in grid[header_idx + 1:]:
            # 데이터 줄 칸 수가 헤더와 다르면(병합 방식 차이) 오른쪽 끝(숫자 열)을 기준으로 정렬
            if len(row) > ncol:
                row = row[len(row) - ncol:]
            elif len(row) < ncol:
                row = [""] * (ncol - len(row)) + row
            get = lambda role: next((row[i] for i, r in roles.items() if r == role and i < len(row)), "")
            campus = clean(get("campus")) or last_campus
            last_campus = campus
            if campus and any(x in campus for x in ("소계", "총계", "합계")):
                continue
            if not campus_ok(campus, cfg):
                continue
            unit = clean(get("unit"))
            group = clean(get("group")) or last_group
            last_group = group
            if not unit and group and group not in ("합계", "총계", "계", "소계"):
                unit = group
            if not unit or unit in ("합계", "총계", "계", "소계") or group in ("합계", "총계", "소계"):
                continue
            track = clean(get("track")) or heading or last_track
            last_track = track
            rec = {
                "track": track,
                "unit": unit,
                "campus": campus,
                "quota": to_int(get("quota")),
                "applicants": to_int(get("applicants")),
                "rate": to_rate(get("rate")),
            }
            if rec["quota"] and rec["applicants"] is not None:
                calc = round(rec["applicants"] / rec["quota"], 2)
                if rec["rate"] is None or abs(rec["rate"] - calc) > max(0.05, calc * 0.02):
                    rec["rate"] = calc  # 표시값과 계산값이 다르면 계산값 우선(열 밀림 방지)
            if rec["rate"] is None and rec["applicants"] is None:
                continue
            if not track_ok(track, cfg):
                continue
            records.append(rec)
    return records


def extract_update_time(html: str):
    """페이지 내 '2026-09-08 16:30 기준' 류 문자열에서 대학측 갱신 시각 추출 (없으면 None)."""
    text = clean(BeautifulSoup(html, "html.parser").get_text(" "))
    # 1순위: '2026-09-08 오후 4:30 현황' (진학어플라이 형식)
    m = re.search(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})\.?\s*(오전|오후)?\s*(\d{1,2}):(\d{2})\s*(?:현황|기준)", text)
    if not m:
        m = re.search(r"(20\d{2})[.\-/년 ]+(\d{1,2})[.\-/월 ]+(\d{1,2})[일 ]*[^\d]{0,12}(오전|오후)?\s*(\d{1,2})[:시 ]+(\d{2})\s*(?:분)?\s*(?:현황|기준)", text)
    if not m:
        return None
    y, mo, d, ampm, h, mi = m.groups()
    y, mo, d, h, mi = int(y), int(mo), int(d), int(h), int(mi)
    if ampm == "오후" and h < 12:
        h += 12
    if ampm == "오전" and h == 12:
        h = 0
    try:
        return datetime(y, mo, d, h, mi, tzinfo=KST).isoformat()
    except ValueError:
        return None


def extract_period(html: str):
    """'원서접수 기간 : 2026. 9. 8(화) 10:00 ∼ 9. 11(금) 18:00' → (start, end) ISO"""
    text = clean(BeautifulSoup(html, "html.parser").get_text(" "))
    m = re.search(r"접수\s*기간\s*[:：]?\s*(20\d{2})[.\s년]*(\d{1,2})[.\s월]*(\d{1,2})[.\s일]*\([^)]*\)\s*(\d{1,2}):(\d{2})\s*[~∼-]\s*(?:(20\d{2})[.\s년]*)?(\d{1,2})[.\s월]*(\d{1,2})[.\s일]*\([^)]*\)\s*(\d{1,2}):(\d{2})", text)
    if not m:
        return None, None
    y, m1, d1, h1, mi1, y2, m2, d2, h2, mi2 = m.groups()
    try:
        st = datetime(int(y), int(m1), int(d1), int(h1), int(mi1), tzinfo=KST)
        en = datetime(int(y2 or y), int(m2), int(d2), int(h2), int(mi2), tzinfo=KST)
        return st.strftime("%Y-%m-%d %H:%M"), en.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return None, None


def extract_notice(html: str):
    """공개 주기 안내 문장(업데이트/공개/Update 포함) 최대 3줄."""
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    lines = [clean(l) for l in text.split("\n")]
    out = [l for l in lines if l and re.search(r"업데이트|Update|공개|일차\s*:", l, re.I) and len(l) < 80]
    return out[:4]


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
    last_path = ROOT / "scraper" / ".last_fetch.json"
    last_fetch = load_json(last_path, {})
    prev_status = latest_prev.get("status", {})

    for u in cfg["universities"]:
        name = u["name"]
        if args.only and name != args.only:
            continue
        per_source, errors, page_time = {}, [], None
        sched = dict(u.get("schedule") or {})
        notice = []
        for s in u.get("sources", []):
            url = (s.get("url") or "").strip()
            if not url or url.upper().startswith("TODO"):
                continue
            gap = float(s.get("interval_min") or 0) * 60
            lf = last_fetch.get(url)
            if gap and lf and (now_kst() - datetime.fromisoformat(lf)).total_seconds() < gap:
                print(f"[{name}] {s['type']}: {int(gap//60)}분 간격 대기 중 (직전 값 유지)", file=sys.stderr)
                continue
            try:
                last_fetch[url] = now_kst().isoformat()
                html = fetch(url)
                if args.dump == name:
                    DEBUG.mkdir(exist_ok=True)
                    (DEBUG / f"{name}_{s['type']}.html").write_text(html, encoding="utf-8")
                ucfg = dict(cfg)
                if u.get("campus_include"):
                    ucfg["campus_include"] = u["campus_include"]
                recs = parse_tables(html, ucfg)
                if not recs:
                    errors.append(f"{s['type']}: 표 인식 실패(0행) — iframe/JS 렌더링 페이지일 수 있음")
                    continue
                per_source[s["type"]] = recs
                pt = extract_update_time(html)
                # 안내문의 과거 날짜(예: '1일차: 9.8 17:00')를 잘못 잡은 경우는 버림 (수집 시각보다 24시간 이상 오래된 값)
                if pt and (now_kst() - datetime.fromisoformat(pt)).total_seconds() > 24 * 3600:
                    pt = None
                page_time = page_time or pt
                st_, en_ = extract_period(html)
                if st_ and (not sched.get("start") or "TODO" in str(sched.get("start"))):
                    sched["start"] = st_
                if en_ and (not sched.get("end") or "TODO" in str(sched.get("end"))):
                    sched["end"] = en_
                notice = notice or extract_notice(html)
                print(f"[{name}] {s['type']}: {len(recs)}행", file=sys.stderr)
            except Exception as e:  # noqa
                errors.append(f"{s['type']}: {type(e).__name__}: {e}")
            time.sleep(0.8)  # 서버 부하 배려

        merged = cross_validate(name, per_source)
        if merged:
            # 이번 수집에 성공한 대학은 예전 항목(캠퍼스·전형 필터로 빠진 것 등)을 모두 교체
            for k in [k for k in all_items if k.startswith(name + "|")]:
                del all_items[k]
        if not merged and not errors and any((x.get("interval_min") or 0) for x in u.get("sources", [])):
            # 간격 대기 중이라 이번 회차는 건너뜀 → 직전 상태 그대로 유지
            if name in prev_status:
                status[name] = prev_status[name]
                continue
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
            "schedule": sched,
            "notice": notice,
        }
        if not merged:
            print(f"[{name}] 수집 실패: {errors or '소스 URL 미설정'}", file=sys.stderr)
            if any("403" in e for e in errors):
                print(f"[{name}]   → 403은 사이트가 해외(GitHub) IP를 차단하는 경우가 대부분입니다. 한국 PC에서 scraper/run_local.py 로 수집하세요.", file=sys.stderr)

    last_path.write_text(json.dumps(last_fetch), encoding="utf-8")
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
