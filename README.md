# 2027학년도 수시 실시간 경쟁률 대시보드

서울 소재 14개 대학(성균관·한국외대·중앙·경희·한양·명지·홍익·건국·동국·세종·서울시립·국민·숭실·광운)의 수시 **논술·학생부(교과/종합) 전형** 경쟁률을 GitHub Actions로 자동 수집하고, GitHub Pages에서 폴드7·PC 브라우저로 확인하는 앱입니다.

```
config/sources.json     대학별 수집 URL + 전형 필터(track_include/exclude)  ← URL 반드시 채울 것
scraper/run_local.py    LG 그램에서 1분 간격 수집·푸시 (GitHub 크론은 최소 5분)
config/humanities.json  인문계열 판정 키워드
config/prev_year.csv    전년(2026학년도) 최종 경쟁률 — 채우면 '전년比' 열과 알림 기준이 생김
scraper/scrape.py       수집·교차검증 스크립트
.github/workflows/      5분 간격 크론 (GitHub 최소 간격)
docs/index.html         대시보드 (GitHub Pages)
docs/data/*.json        수집 결과 (자동 커밋)
```

## 설치 (약 15분)

1. GitHub에 새 저장소를 만들고 이 폴더 전체를 올립니다 (private 가능).
2. **Settings → Pages → Source: Deploy from a branch → Branch: main, Folder: /docs** 저장.
3. **Settings → Actions → General → Workflow permissions: Read and write** 로 변경 (커밋 권한).
4. `config/sources.json`의 `TODO`를 실제 URL로 교체 (성균관·숭실·홍익·한양은 2027 진학어플라이 URL을 이미 넣어 두었음):
   - 가장 빠른 방법: 진학사 정리 글 https://www.jinhak.com/jh/high3/univ-entrance-info/ipsi-analysis/ipsi-strategy/100000727 에서 대학별 '경쟁률 보기' 링크를 복사 (14개교 모두 있음).
   - 진학어플라이 대학은 `addon.jinhakapply.com/RatioV1/RatioH/Ratio########.html` 형태 → `type: jinhak`. 이 페이지 구조(전형별 제목 + 모집단위 표, colspan 그룹열, '오후 4:30 현황' 시각, '원서접수 기간' 문구)는 파서가 이미 대응합니다.
   - 유웨이어플라이 대학(한국외대, 국민대 등)은 `ratio.uwayapply.com` 이 자동 수집을 차단하므로 **입학처 페이지 URL**을 `type: univ` 에 넣으세요.
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

## 1분 갱신에 대해
- 대시보드는 60초마다 데이터를 다시 읽습니다(상단에 카운트다운).
- 그러나 **데이터 자체는 수집기가 돌 때만 바뀝니다.** GitHub Actions 크론은 최소 5분 간격이고 실제로는 더 늦어질 수 있습니다.
- 진짜 1분 갱신이 필요하면 LG 그램에서 저장소를 clone한 뒤 `python scraper/run_local.py --interval 60` 을 켜 두세요. 로컬에서 수집·푸시하고 GitHub Pages가 1~2분 내 반영합니다. 마감일에는 이 방식을 권합니다.
- 대학이 경쟁률을 하루 몇 번만 공개하는 경우, 1분 수집을 해도 숫자는 그 주기로만 바뀝니다.

## 대학 카드 (접수 상태·발표 시각표)
- 화면 상단 카드는 대학별 전체 경쟁률(선택 전형·인문계열 합산), 접수 상태(접수예정/접수중/마감), 시작·마감 일시, 경쟁률 발표 시각표를 보여줍니다.
- 이 정보는 `config/sources.json`의 각 대학 `schedule` 항목에서 옵니다. `start`/`end`(접수 시작·마감), `publish`({일: [시각,...]}), 실시간 공개 대학은 `realtime: true`. **각 대학 입학처 공지대로 채워야 하며 기본값은 자리표시자입니다.**
- 시각표에서 지난 발표는 흐리게, 다음 발표는 파란색으로 표시됩니다. 카드를 누르면 아래 표가 그 대학으로 좁혀지고, 다시 누르면 전체로 돌아옵니다.
- 수집에 실패한 대학은 '확인필요'로 표시되고 오류 내용이 카드에 나옵니다.

## 동작 원리
- `config/sources.json`의 `track_include`/`track_exclude`로 전형을 거릅니다. 기본값은 논술·학생부(교과/종합) 일반전형만 남기고 특기자·농어촌·기회균형 등은 제외합니다. 대학마다 전형명이 달라 누락되면 키워드를 추가하세요.
- 상단 **미달·저경쟁 10**은 현재 필터(대학·인문계열·검색어) 안에서 경쟁률 낮은 순 10개이며 1.00 미만은 미달로 표시합니다. 마감 전 저경쟁은 마감 직전 몰림으로 뒤집히는 일이 흔하니 참고 지표로만 쓰세요.
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
