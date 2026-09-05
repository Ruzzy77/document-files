# Document Files

네이티브 앱을 열지 않고 문서 바이트에서 구조와 값을 추출하며, 로컬 문서를 검사하고 지원 형식을 변환·렌더링하고 HWPX 산출물을 생성·편집·검증하는 플러그인입니다.

## 담당 범위

- PDF, DOCX, PPTX, XLSX, HWP, HWPX, HTML, Markdown, 일반 텍스트의 구조 보존 추출
- 형식에 공통으로 적용되는 구조·시맨틱 역할·명시적 값의 페이지 단위 JSON 추출
- 로컬 경로를 포함하지 않는 분석 작업·결과 계약과 여러 host에서 같은 Python 구현 사용
- 이미지 중심 문서의 부분 추출 표시와 대화형 작업의 제한된 vision 보조
- 본문·구조·시각 내용·읽기 순서를 분리한 coverage 보고
- HWP/HWPX의 텍스트·Markdown 변환과 HWP→HWPX 변환
- 요청된 HWP/HWPX SVG·PDF 렌더링과 HWPX HTML 미리보기
- HWPX 생성, 문구·표 셀 편집, 패키지·구조 검증

원본은 읽기 전용으로 다루고, 쓰기 결과는 별도 경로에 만듭니다. HWP 원본 편집, HWPX→HWP 변환, 암호나 문서 보호 우회는 지원하지 않습니다.

분석기의 공통 입력은 형식·미디어 유형·바이트 크기·SHA-256으로 문서를 식별하는 `document-files.analysis-job.v1` 작업과 별도로 전달되는 바이트 스트림입니다. 결과는 `document-files.analysis-result.v1`으로 반환합니다. 두 계약에는 로컬 경로나 전송 방식이 들어가지 않습니다. 분석기는 형식 라이브러리가 파일을 여러 번 안전하게 열 수 있도록 바이트를 프로세스 전용 임시 파일로 복사하고, 추출이 끝나면 삭제합니다.

Corpus의 기존 로컬 연동은 원본 경로 대신 상속한 읽기 전용 파일 descriptor를 사용하는 엄격한 JSONL 경계를 유지합니다. 이 경계는 기존 호출자를 위한 로컬 전송 방식이며, 문서 분석 계약 자체의 입력 형식은 아닙니다.

Corpus와 함께 사용할 때 역할은 분리됩니다. Document Files는 형식별 파싱과 추출 범위를 담당하고, Corpus와 Sync는 Source 등록·캡처, revision과 projection 식별, Source unit ID, anchor, 검색과 Context를 담당합니다. Sync는 Document Files를 같은 Python 환경에서 격리 subprocess로 실행하고 projection만 원격 Corpus에 전달합니다.

descriptor의 adapter ID, 구현 version과 config hash는 결과가 만들어진 조건을 정확히 기록합니다.
이 값은 자동 재분석 조건이 아닙니다. 각 형식의 `reanalysis_generation`은 내용이 같은 기존
문서에서도 추출 결과를 다시 만들어야 하는 변경에만 올립니다. 코드 정리, 패키징, 실행 환경과
일반 구성 변경은 projection의 provenance에는 남을 수 있지만 재분석 세대는 바꾸지 않습니다.

## 실행 방식

Sync와 로컬 Codex는 로컬 package를 사용하고 원격 Codex는 통합 plugin, 개인 ChatGPT는 같은
정본에서 만든 Personal Skill의 host runtime을 사용합니다. 필요한 라이브러리나 호환 `rhwp`가
없으면 지원 가능한 순수 parser 결과와 coverage를 반환하거나 `runtime_unavailable`로 중단하며
자동 원격 폴백하지 않습니다. 렌더 결과에는
`nativeRenderChecked: false`가 기록되며 화면 충실도의 주 검증으로 간주하지 않습니다.

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

`inspect`, `extract`, `extract-structure`는 같은 분석 결과를 각각 요약·본문·구조로 제공합니다.
필요한 응답 하나를 선택하면 되며, 읽기 전에 세 명령을 순서대로 호출할 필요는 없습니다.
HWP/HWPX의 일반 읽기에는 편집용 `python-hwpx`나 `rhwp`가 필요하지 않습니다.
`inspect`·`extract`의 `completeness`와 호환용 `coverage` 문자열은 추출의 완전성을,
`coverageProfile`은 본문·구조·시각 내용·읽기 순서의 범위를 나타냅니다. 구조 추출에서는 같은
차원별 객체를 기존 `coverage` 필드로 제공합니다. 텍스트 잘림과 구조 페이지의 남은 항목은
추출 실패나 부분 추출과 별도로 확인합니다. Markdown은 선언된 제목·목록·표 셀 위치를 나타내며
원본의 페이지 배치를 복원하지 않습니다.

HWPX 표를 편집할 때에는 `inspect`의 `tableMap.tables`에서 검증된 `sectionPath`·`tableIndex`와
셀의 `row`·`col`을 선택합니다. `selectorBasis="verified-section-xml-table-order"`는 해당 section
XML과 편집기의 표 순서를 대조했다는 뜻입니다. 선택자가 없는 표에는 목록 순서나 `sourceRef`를
대신 넣지 않습니다. 읽은 셀 텍스트가 잘리지 않았는지 확인하고 `expectedOldText`로 대조합니다.
이 값은 첫 문단이 아니라 셀 전체 텍스트이며, 편집 전에 같은 입력 바이트에서 확인합니다.
동일 물리 셀의 중복 지정, 기존 문단 수로 담을 수 없는 새 줄, 중첩 표를 포함하는 바깥 셀의
편집은 값 손실을 막기 위해 거절합니다. 중첩 표 안의 일반 셀은 편집할 수 있습니다. `verify`의
`ok`와 reference 비교의 `tableGeometryPreserved`는 별도 결과이므로 표 구조 보존은 후자도
확인합니다.

다른 런타임에 분석기를 내장할 때에는 `AnalysisJob`과 바이트 스트림을 `analyze_document`에
전달합니다. `AnalysisJob`과 `AnalysisResult`의 `to_dict`·`from_dict`는 실행 위치가 공유하는 직렬화
경계이며, 호출자는 결과의 작업 ID와 입력 해시가 요청과 일치하는지 검증받습니다. 원본 보관과
접근 정책은 분석기가 아니라 호출 계층이 담당합니다.

```bash
launchers/document-files process --describe
```

이 명령은 사람이 읽을 본문을 만드는 용도가 아니라, 읽기 전용 파일 디스크립터를 받아 구조 단위·추출 범위·이슈를 JSONL로 반환하는 내부 경계입니다.

## 백엔드

- `python-docx`, `python-pptx`, `openpyxl`, `pypdf`: Office와 PDF 구조 추출
- `olefile` 0.47: HWP compound-file parser; OpenAI host용 순수 Python fallback을 함께 배포
- `python-hwpx` 6.3.0: HWPX 편집과 왕복 충실도 검사
- `python-hwpx-automation` 7.0.3: HWPX 생성과 품질 검사
- `rhwp` 0.8.6: HWP 복구 추출, HWP→HWPX 변환과 선택적 미리보기

`rhwp`는 다음 명령으로 사용자 캐시에 설치합니다.

```bash
python3 scripts/provision_rhwp.py
```

별도 실행 파일은 `DOCUMENT_FILES_RHWP`로 지정할 수 있습니다. 배포판은 준비 단계에서 플랫폼별
체크섬과 macOS 서명을 확인하며 문서 처리 중 내려받지 않습니다. 일반 parser를 우선하고 `rhwp`는
HWP 보조 경로에만 사용합니다.

실행 경계와 데이터 소유 원칙은 [DESIGN.md](./DESIGN.md)에 있습니다.
