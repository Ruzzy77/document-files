# Document Files

네이티브 앱을 열지 않고 문서 바이트에서 구조와 값을 추출하며, 로컬 문서를 검사하고 지원 형식을 변환·렌더링하고 HWPX 산출물을 생성·편집·검증하는 플러그인입니다.

## 담당 범위

- PDF, DOCX, PPTX, XLSX, HWP, HWPX, HTML, Markdown, 일반 텍스트의 구조 보존 추출
- 형식에 공통으로 적용되는 구조·시맨틱 역할·명시적 값의 페이지 단위 JSON 추출
- 로컬 경로를 포함하지 않는 분석 작업·결과 계약과 교체 가능한 분석 백엔드
- PDF와 Office 계열 문서의 로컬 OCR 및 추출 범위·경고 보고
- 본문·구조·시각 내용·읽기 순서를 분리한 coverage 보고
- 큰 문서의 페이지·이미지 구간을 한 실행 안에서 이어 처리
- HWP/HWPX의 텍스트·Markdown 변환과 HWP→HWPX 변환
- HWP/HWPX의 SVG·PDF 렌더링과 HWPX HTML 미리보기
- HWPX 생성, 문구·표 셀 편집, 패키지·구조 검증

원본은 읽기 전용으로 다루고, 쓰기 결과는 별도 경로에 만듭니다. HWP 원본 편집, HWPX→HWP 변환, 암호나 문서 보호 우회는 지원하지 않습니다.

분석기의 공통 입력은 형식·미디어 유형·바이트 크기·SHA-256으로 문서를 식별하는 `document-files.analysis-job.v1` 작업과 별도로 전달되는 바이트 스트림입니다. 결과는 `document-files.analysis-result.v1`으로 반환합니다. 두 계약에는 로컬 경로나 전송 방식이 들어가지 않습니다. 현재 포함된 로컬 백엔드는 형식 라이브러리가 파일을 여러 번 안전하게 열 수 있도록 바이트를 프로세스 전용 임시 파일로 복사하고, 추출이 끝나면 삭제합니다. 다른 실행 환경은 같은 `AnalyzerBackend`와 직렬화 계약을 구현해 분석기를 교체할 수 있습니다.

Corpus의 기존 로컬 연동은 원본 경로 대신 상속한 읽기 전용 파일 descriptor를 사용하는 엄격한 JSONL 경계를 유지합니다. 이 경계는 기존 호출자를 위한 로컬 전송 방식이며, 문서 분석 계약 자체의 입력 형식은 아닙니다.

Corpus와 함께 사용할 때 역할은 분리됩니다. Document Files는 형식별 파싱·OCR·추출 범위를 담당하고, Corpus와 동기화 계층은 Source 등록·캡처, 로컬·원격 처리 정책, revision과 projection 식별, Source unit ID, anchor, 검색과 Context를 담당합니다.

descriptor의 adapter ID, 구현 version과 config hash는 결과가 만들어진 조건을 정확히 기록합니다.
이 값은 자동 재분석 조건이 아닙니다. 각 형식의 `reanalysis_generation`은 내용이 같은 기존
문서에서도 추출 결과를 다시 만들어야 하는 변경에만 올립니다. 코드 정리, 패키징, 실행 환경과
일반 구성 변경은 projection의 provenance에는 남을 수 있지만 재분석 세대는 바꾸지 않습니다.

## 실행 방식

문서 읽기와 렌더링은 백그라운드에서 수행합니다. 배경 렌더 결과에는 `nativeRenderChecked: false`가 기록되며, 네이티브 앱에서 본 화면과 같다고 간주하지 않습니다.

## 주요 명령

```bash
launchers/document-files capabilities
launchers/document-files inspect input.pdf
launchers/document-files extract input.docx --format text
launchers/document-files extract input.pptx --format markdown
launchers/document-files extract-structure input.xlsx --max-units 500
launchers/document-files inspect input.hwp
launchers/document-files convert input.hwp output.hwpx
launchers/document-files render input.hwpx output.pdf
launchers/document-files verify output.hwpx
```

Corpus 연동용 구조 추출 계약은 같은 실행 파일의 `process` 명령을 사용합니다.

다른 프로젝트에서 구조와 값을 직접 사용할 때에는 `extract-structure` 명령이나
`document_extract_structure` 도구를 사용합니다. 결과는 원본 형식의 위치 정보인
`sourceStructure`와 형식 공통 `semanticRole`·`semantic`을 함께 제공합니다. XLSX 셀은
문자열·수·불리언·날짜·수식과 저장된 계산값을 구분하며, 표 셀 좌표·병합 범위·필드
메타데이터도 원본에 기록된 범위에서만 반환합니다. 수식을 실행하거나 인접 셀 관계를
추정하지 않습니다. 큰 결과는 `unitPage.nextOffset`으로 이어서 읽습니다.

서비스나 다른 런타임에 분석기를 내장할 때에는 `AnalysisJob`과 바이트 스트림을
`analyze_document` 또는 `extract_structure_from_stream`에 전달합니다. `AnalysisJob`과
`AnalysisResult`의 `to_dict`·`from_dict`는 로컬·원격 구현이 공유하는 직렬화 경계이며,
호출자는 결과의 작업 ID와 입력 해시가 요청과 일치하는지 검증받습니다. 접근 승인,
`local_only` 같은 처리 정책과 원본 보관은 분석기가 아니라 호출 계층이 담당합니다.

```bash
launchers/document-files process --describe
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
