# 설정 (환경변수)

모두 선택 사항이다. `prep.py`와 `render.py`에 같은 값을 줘야 문단 id와 좌표가 어긋나지 않는다.

```bash
ANNOT_TOP=30 ANNOT_BOT=500 uv run S/scripts/prep.py deck.pdf
ANNOT_TOP=30 ANNOT_BOT=500 uv run S/scripts/render.py deck.pdf
```

| 변수 | 기본값 | 용도 |
|---|---|---|
| `ANNOT_TOP`, `ANNOT_BOT` | 쪽 높이의 3.5%, 96.5% | 머리띠 아래 끝, 바닥글 위 끝의 y좌표(pt). 이 밖에는 주석이 놓이지 않고, 그 안의 글은 문단으로 잡히지 않는다 |
| `ANNOT_SKIP_TEXT` | 없음 | 쪽 위아래 끝에 반복되는 고정 문구(학교 이름, 과목 코드). 쉼표로 여러 개. 문단 목록에서 뺀다 |
| `ANNOT_SCALE` | `1.0` | 주석 글자 크기와 줄 폭의 배율. 여백이 넉넉한 16:9 슬라이드는 1.3 정도가 읽기 좋다 |
| `ANNOT_FONT`, `ANNOT_FONT_BOLD` | 자동 탐색 | 본문·굵은 글꼴 파일(ttf/otf) 경로 |
| `ANNOT_FONT_MATH` | 자동 탐색 | 본문 글꼴에 없는 기호를 그릴 글꼴 파일 경로 |
| `ANNOT_SUM_LABEL` | `정리` | 쪽 한 줄 정리 앞에 붙는 머리말 |
| `ANNOT_SUFFIX` | `_주석` | 결과 파일 이름 뒤에 붙는 말(`out/<이름>_주석.pdf`) |
| `ANNOT_TITLE_SIZE` | `30` | 이 크기(pt) 이상인 문단을 슬라이드 제목으로 본다 |
| `ANNOT_REF_HEIGHT` | `540` | 쪽 크기 보정의 기준 높이. `0`이면 보정하지 않는다 |

## 글꼴

지정하지 않으면 시스템 글꼴 폴더에서 Noto Sans KR → Pretendard → Noto Sans CJK KR → 나눔고딕 → 맑은 고딕 → AppleGothic 순으로 찾는다. 하나도 없으면 PyMuPDF에 들어 있는 CJK 글꼴(Droid Sans Fallback)로 그린다. 이 글꼴은 굵은 글씨가 없어 `**굵게**`가 보통 굵기로 나온다.

보기 좋은 결과를 원하면 [Noto Sans KR](https://fonts.google.com/noto/specimen/Noto+Sans+KR)의 Regular와 Bold를 설치한다. 설치하지 않고 파일만 받아 경로를 지정해도 된다. `ANNOT_FONT=cjk`는 내장 글꼴을 강제로 쓴다(다른 컴퓨터에서 같은 결과를 내야 할 때).

## 쪽 크기 보정

글자 크기와 여백 기준은 높이 540pt 슬라이드(PowerPoint 4:3 720×540, 16:9 960×540)에 맞춰져 있다. 가로로 긴 쪽의 높이가 540pt와 10% 넘게 다르면(Beamer 363×272, Google Slides 720×405, Keynote 1920×1080 등) 쪽을 540pt 높이로 확대·축소한 사본 위에서 배치를 계산하고, 그린 주석을 원본 쪽 크기에 맞춰 다시 얹는다. 원본 쪽의 크기와 내용은 바뀌지 않는다.

세로로 긴 쪽(A4 강의 노트 등)은 보정하지 않는다. 회전 속성(/Rotate)이 있는 쪽은 보이는 모양 그대로 회전 속성만 0으로 정리한 뒤 처리한다.

## 한계

- 글자 층이 없는 스캔 PDF는 문단이 잡히지 않아 `tr`와 `hl`을 쓸 수 없다. 좌표나 `null`을 가리키는 필기와 `sum`만 가능하다. 먼저 OCR로 글자 층을 넣는다.
- 배경이 어두운 슬라이드는 빈자리 판정이 맞지 않는다(`dark_page` 경고).
- 빈자리가 거의 없는 쪽은 번역이 번호 패널로 밀리거나 필기가 놓이지 않는다. 분량을 줄이는 것이 답이다.
