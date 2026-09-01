# Document Files

네이티브 앱을 열지 않고 로컬 문서를 읽고 추출하며, 지원 형식을 변환·렌더링하고 HWPX 산출물을 생성·편집·검증하는 플러그인입니다.

## 담당 범위

- PDF, DOCX, PPTX, XLSX, HWP, HWPX, HTML, Markdown, 일반 텍스트의 구조 보존 추출
- PDF와 Office 계열 문서의 로컬 OCR 및 추출 범위·경고 보고
- 본문·구조·시각 내용·읽기 순서를 분리한 coverage 보고
- 큰 문서의 페이지·이미지 구간을 한 실행 안에서 이어 처리
- HWP/HWPX의 텍스트·Markdown 변환과 HWP→HWPX 변환
- HWP/HWPX의 SVG·PDF 렌더링과 HWPX HTML 미리보기
- HWPX 생성, 문구·표 셀 편집, 패키지·구조 검증

원본은 읽기 전용으로 다루고, 쓰기 결과는 별도 경로에 만듭니다. HWP 원본 편집, HWPX→HWP 변환, 암호나 문서 보호 우회는 지원하지 않습니다.

Corpus 연동에서는 원본 경로 대신 상속한 읽기 전용 파일 descriptor만 받습니다. 형식 라이브러리가 파일을 여러 번 안전하게 열 수 있도록 프로세스 전용 임시 파일로 한 번 복사하며, 추출 종료 시 이 사본을 삭제합니다.

Corpus와 함께 사용할 때 역할은 분리됩니다. Document Files는 형식별 파싱·OCR·추출 범위를 담당하고, Corpus는 Source 등록·캡처, revision과 projection 식별, Source unit ID, anchor, 검색과 Context를 담당합니다.

## 실행 방식

문서 읽기와 렌더링은 백그라운드에서 수행합니다. 배경 렌더 결과에는 `nativeRenderChecked: false`가 기록되며, 네이티브 앱에서 본 화면과 같다고 간주하지 않습니다.

## 주요 명령

```bash
bin/document-files capabilities
bin/document-files inspect input.pdf
bin/document-files extract input.docx --format text
bin/document-files extract input.pptx --format markdown
bin/document-files inspect input.hwp
bin/document-files convert input.hwp output.hwpx
bin/document-files render input.hwpx output.pdf
bin/document-files verify output.hwpx
```

Corpus 연동용 구조 추출 계약은 같은 실행 파일의 `process` 명령을 사용합니다.

```bash
bin/document-files process --describe
```

이 명령은 사람이 읽을 본문을 만드는 용도가 아니라, 읽기 전용 파일 디스크립터를 받아 구조 단위·추출 범위·이슈를 JSONL로 반환하는 내부 경계입니다.

## 백엔드

- `python-docx`, `python-pptx`, `openpyxl`, `pypdf`: Office와 PDF 구조 추출
- macOS PDFKit·Vision: PDF 및 포함 이미지의 로컬 OCR
- `python-hwpx` 6.3.0: HWPX 읽기·편집과 왕복 충실도 검사
- `python-hwpx-automation` 7.0.3: HWPX 생성과 품질 검사
- `rhwp` 0.8.2: HWP/HWPX 추출·변환·렌더링

`rhwp`는 다음 명령으로 사용자 캐시에 설치합니다.

```bash
python3 scripts/provision_rhwp.py
```

별도 실행 파일은 `DOCUMENT_FILES_RHWP`로 지정할 수 있습니다. 편집·생성 계획과 검증 절차는 `skills/document-files/references/operations.md`에 있습니다.
