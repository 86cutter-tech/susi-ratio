# 2027학년도 수시 실시간 경쟁률 대시보드

서울 소재 4년제 남녀공학 대학의 수시 경쟁률을 GitHub Actions로 자동 수집하고, GitHub Pages에서 폴드7·PC 브라우저로 확인하는 앱입니다.

```
config/sources.json     대학별 수집 URL (입학처 / 진학어플라이 / 유웨이어플라이)  ← 반드시 채울 것
config/humanities.json  인문계열 판정 키워드
config/prev_year.csv    전년(2026학년도) 최종 경쟁률 — 채우면 '전년比' 열과 알림 기준이 생김
scraper/scrape.py       수집·교차검증 스크립트
.github/workflows/      10분 간격 크론
docs/index.html         대시보드 (GitHub Pages)
docs/data/*.json        수집 결과 (자동 커밋)
```

## 설치 (약 15분)

1. GitHub에 새 저장소를 만들고 이 폴더 전체를 올립니다 (private 가능).
2. **Settings → Pages → Source: Deploy from a branch → Branch: main, Folder: /docs** 저장.
3. **Settings → Actions → General → Workflow permissions: Read and write** 로 변경 (커밋 권한).
4. `config/sources.json`의 `TODO`를 실제 URL로 교체:
   - 각 대학 입학처 사이트의 "수시 경쟁률" 페이지를 열고 주소창 URL 복사.
   - 진학어플라이(ratio.jinhakapply.com) / 유웨이어플라이(ratio.uwayapply.com)의 해당 대학 경쟁률 페이지 URL 복사.
   - 페이지가 **iframe** 안에 표를 보여주면 (우클릭 → 프레임 소스 보기) iframe의 src URL을 넣습니다.
   - 표가 자바스크립트로 그려지는 페이지(개발자도구 Network 탭에서 JSON/XHR로 오는 경우)는 그 API URL을 넣어보세요. 그래도 안 되면 다른 소스를 쓰세요.
5. **Actions → 수시 경쟁률 수집 → Run workflow**로 1회 수동 실행 → 로그에서 `[대학명] univ: N행`이 찍히는지 확인.
6. `https://<계정>.github.io/<저장소>/` 접속. 폴드7 홈 화면에 추가하면 앱처럼 씁니다.

## 로컬 검증 (파서 보정이 필요할 때)

```bash
pip install -r scraper/requirements.txt
python scraper/scrape.py --only 한국외대 --dump 한국외대   # debug/한국외대_univ.html 저장
```
- `표 인식 실패(0행)`: 헤더 텍스트가 `scrape.py`의 `COL_KEYS`와 안 맞는 경우. 저장된 HTML에서 헤더 명칭을 확인해 `COL_KEYS`에 추가.
- 한글 깨짐: `fetch()`의 인코딩 보정이 실패한 경우. `r.encoding = "cp949"`로 고정해 보세요.

## 동작 원리
- 수집기는 페이지의 모든 `<table>`을 훑어 `전형 / 모집단위 / 모집인원 / 지원인원 / 경쟁률` 열을 헤더 키워드로 자동 인식합니다 (rowspan·colspan 처리, 합계 행 제외).
- 같은 대학에 소스가 둘 이상이면 (대학, 전형, 모집단위) 키로 합치고, 지원인원이 다르면 **불일치** 표시 후 큰 값(더 최신)을 채택합니다.
- 지원인원이 바뀔 때만 `history.json`에 점을 추가하므로 추이 그래프는 실제 변동만 보여줍니다.
- 대시보드는 5분마다 데이터를 다시 읽고, ★ 표시한 관심 학과의 지원인원이 바뀌면 화면 토스트 + (허용 시) 브라우저 알림을 보냅니다. 전년 경쟁률을 넘어서는 순간도 알려줍니다.
- 관심 학과 목록은 브라우저(localStorage)에 저장되므로 폴드7과 PC에서 각각 지정해야 합니다.

## 주의
- 대학별 경쟁률 공개 주기가 다릅니다(하루 2~3회 ~ 실시간). 마지막 공개값과 최종 경쟁률은 다를 수 있습니다.
- GitHub Actions 크론은 최소 5분 간격이며 실제로는 수 분 지연될 수 있습니다. 마감 직전 눈치 지원용으로는 입학처 페이지를 직접 함께 확인하세요.
- 0.8초 간격으로 요청하도록 했지만, 대행사 사이트가 차단하면 해당 소스를 빼고 입학처만 쓰세요.
- 접수 기간이 끝나면 `.github/workflows/scrape.yml`의 `schedule` 항목을 주석 처리해 크론을 멈추세요.
