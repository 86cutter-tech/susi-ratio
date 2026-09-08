#!/usr/bin/env python3
"""LG 그램에서 1분 간격으로 수집해 GitHub에 푸시 (GitHub Actions 5분 크론의 보조).
사용: 저장소 폴더에서  python scraper/run_local.py --interval 60
    --no-push 를 주면 푸시 없이 로컬 docs/ 만 갱신 (그럴 땐  python -m http.server -d docs 8000  으로 열람)
"""
import argparse, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser(); ap.add_argument("--interval", type=int, default=60); ap.add_argument("--no-push", action="store_true")
a = ap.parse_args()
while True:
    t = time.time()
    subprocess.run([sys.executable, str(ROOT / "scraper" / "scrape.py")], cwd=ROOT)
    if not a.no_push:
        subprocess.run(["git", "pull", "--rebase", "-q"], cwd=ROOT)
        subprocess.run(["git", "add", "docs/data"], cwd=ROOT)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode:
            subprocess.run(["git", "commit", "-qm", "data: local " + time.strftime("%m-%d %H:%M")], cwd=ROOT)
            subprocess.run(["git", "push", "-q"], cwd=ROOT)
    time.sleep(max(5, a.interval - (time.time() - t)))
