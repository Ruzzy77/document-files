# Document Files

Document Files는 문서의 구조·의미·값을 추출하는 독립 Python 제품입니다. 내부 AI가
해석·추가 읽기·보완·검사를 수행하고, 프로그램과 에이전트는 같은 엔진의 Python API·CLI·MCP·HTTP를
사용합니다. 기존 문서 읽기·변환·HWPX 제작/편집과 단일 문서 Skill도 함께 제공합니다.

소스 정본은 [Ruzzy77/document-files](https://github.com/Ruzzy77/document-files)입니다.
Personal Agent Toolkit과 Sync는 정식 출시와 소비 측 확인 후 지정 릴리스로 전환합니다. 개인 데이터나
회사별 양식은 제품 저장소에 넣지 않습니다.

## 현재 목표와 사용 상태

**먼저 DGX Spark에서 문서의 구조·값·관계·출처를 정확하게 추출하는 데 집중합니다.**
Linux ARM64 인식 팩은 CPU에서, 내부 AI는 CUDA 런타임에서 실행합니다. 그다음 개인용 Mac
실행을 확인합니다. Windows·Intel Mac·Linux x64 검증, 정식 배포와 Toolkit·Sync·클라이언트
전환은 뒤로 미룹니다. 기존 기능과 플랫폼 코드는 유지하지만 지원 완료로 표시하지 않습니다.

현재 소스 버전은 **1.8.0 개발 버전**이며 정식 GitHub 릴리스는 아직 없습니다.
Spark의 실제 개발 사례에서는 병합 머리글과 페이지 간 표를 처리했지만, 데이터 행 보존과
중복 판단에 남은 결함이 있습니다. `complete` 응답이나 시험 통과만으로 정확성이 입증되지는
않습니다. 확인 범위와 다음 수정 대상은 [현재 상태](SUPPORT.md)에 정리합니다.

- 개발·Python 연동: 이 저장소에서 `uv sync --frozen --python 3.12`로 준비합니다.
  저장소 실행기는 이 `.venv`를 사용하며 모델과 인식 팩은 별도로 준비해야 합니다.
- Spark 실행: [운영 안내](docs/operations.md)의 명시적 팩·프로필·예산을 사용합니다.
  GPU 개발 실행과 실제 HTTP 서비스 설치 검증은 구분합니다.
- 실행 환경 포함 묶음과 Codex·Claude Code·Claude Desktop·ChatGPT 연결 패키지는 빌더가
  있지만, 현재 내려받아 쓰도록 권할 정식 배포본은 없습니다. 기존 설치는 바꾸지 않습니다.

### 문서 안내

| 확인할 내용 | 문서 |
| --- | --- |
| 제품의 역할과 구성 | [제품 구조](docs/product-architecture.md) |
| PDF·표·값·의미를 읽는 상세 동작 | [추출 엔진](docs/extraction-engine.md) |
| Python·CLI·MCP 호출과 결과 | [연동 API](docs/python-api.md) |
| Spark 실행, 중단·재개와 팩 관리 | [운영 안내](docs/operations.md) |
| 확인된 결과, 남은 결함과 완료 기준 | [현재 상태](SUPPORT.md) |
| 수정·시험·main 작업 방식 | [개발 안내](CONTRIBUTING.md) |
| 추후 팩 제작과 공개 | [배포 구성](deployment/README.md), [릴리스 절차](deployment/RELEASE.md) |

과거 실행 경과는 Git과 비공개 실행 기록에 보존합니다. 구현 문서에는 실행할 때마다 경과를
덧붙이지 않고 해당 기능의 현재 동작과 제약을 고칩니다. 별도 인수인계 문서를 작업 기준으로
사용하지 않습니다.

기존 읽기·제작·편집·변환은 유지합니다. 내부 AI는 명시적으로 선택한 모델만 사용하며 문서
처리 중 모델 다운로드나 다른 서버로의 자동 전환을 하지 않습니다. 원문 관측과 AI 해석은
별도로 보존하고, 확인하지 못한 내용은 부분 결과에 남깁니다.

## 단일 문서 Skill

사용자에게는 `Document Files` 하나만 노출합니다. DOCX, PDF, XLSX·CSV, PPTX의 작성·편집과
Google Docs·Sheets·Slides 처리 방법은 Skill 내부의 조건부 참고 자료로 유지하며 별도
`documents`, `pdf`, `spreadsheets`, `presentations` Skill이나 별칭으로 배포하지 않습니다.

로컬 문서 작성·편집은 형식에 맞는 호스트 라이브러리를 사용합니다. Google native 문서 요청은
현재 연결 도구로 해당 문서를 직접 다루고, 새 문서를 만들 때에는 지원되는 native 변환 경로를
사용합니다. 로컬 파일 요청을 업로드·공유 요청으로 확대하지 않습니다. 열린 Excel 앱 제어는
별도 live-control 기능의 범위이며, 단일 Skill 통합을 이유로 제거하지 않습니다.

아래 분석기와 명령 목록은 로컬 실행 계약입니다. Skill의 일반 문서 작성 기능 전체를 분석기
명령이나 MCP 도구가 직접 제공한다는 뜻은 아닙니다.

## 로컬 분석·HWPX 실행 기능

- PDF, DOCX, PPTX, XLSX, HWP, HWPX, HTML, Markdown, 일반 텍스트의 구조 보존 추출
- 형식에 공통으로 적용되는 구조·시맨틱 역할·명시적 값의 페이지 단위 JSON 추출
- 로컬 경로를 포함하지 않는 분석 작업·결과 계약과 여러 host에서 같은 Python 구현 사용
- 이미지 중심 문서의 부분 추출 표시와 대화형 작업의 제한된 vision 보조
- 본문·구조·시각 내용·읽기 순서를 분리한 coverage 보고
- HWP/HWPX의 텍스트·Markdown 변환과 HWP→HWPX 변환
- 요청된 HWP/HWPX SVG·PDF 렌더링과 HWPX HTML 미리보기
- HWPX 생성, 문구·표 셀 편집, 패키지·구조 검증

로컬 원본은 읽기 전용으로 다루고, 쓰기 결과는 별도 경로에 만듭니다. HWP 원본 편집, HWPX→HWP 변환, 암호나 문서 보호 우회는 지원하지 않습니다.

분석기의 공통 입력은 형식·미디어 유형·바이트 크기·SHA-256으로 문서를 식별하는 `document-files.analysis-job.v1` 작업과 별도로 전달되는 바이트 스트림입니다. 결과는 `document-files.analysis-result.v1`으로 반환합니다. 두 계약에는 로컬 경로나 전송 방식이 들어가지 않습니다. 분석기는 형식 라이브러리가 파일을 여러 번 안전하게 열 수 있도록 바이트를 프로세스 전용 임시 파일로 복사하고, 추출이 끝나면 삭제합니다.

Corpus의 기존 로컬 연동은 원본 경로 대신 상속한 읽기 전용 파일 descriptor를 사용하는 엄격한 JSONL 경계를 유지합니다. 이 경계는 기존 호출자를 위한 로컬 전송 방식이며, 문서 분석 계약 자체의 입력 형식은 아닙니다. Windows에서는 원본 경로 대신 별도의 읽기 전용 스냅샷과 크기·SHA-256을 전달하고, 자식 프로세스가 이를 검증한 뒤 읽습니다.

Corpus와 함께 사용할 때 역할은 분리됩니다. Document Files는 형식별 파싱과 추출 범위를 담당하고, Corpus와 Sync는 Source 등록·캡처, revision과 projection 식별, Source unit ID, anchor, 검색과 Context를 담당합니다. Sync는 Document Files를 같은 Python 환경에서 격리 subprocess로 실행하고 projection만 원격 Corpus에 전달합니다.

descriptor의 adapter ID, 구현 version과 config hash는 결과가 만들어진 조건을 정확히 기록합니다.
이 값은 자동 재분석 조건이 아닙니다. 각 형식의 `reanalysis_generation`은 내용이 같은 기존
문서에서도 추출 결과를 다시 만들어야 하는 변경에만 올립니다. 코드 정리, 패키징, 실행 환경과
일반 구성 변경은 projection의 provenance에는 남을 수 있지만 재분석 세대는 바꾸지 않습니다.

## 실행 방식

Sync와 로컬 Codex는 로컬 package를 사용하고 원격 Codex는 통합 plugin, 개인 ChatGPT는 같은
정본에서 만든 단일 `Document Files` Personal Skill의 host runtime을 사용합니다. 기존 설치는 해당 클라이언트의 지원되는 갱신 경로를 사용합니다.
실행 코드나 호스트 라이브러리를 제거하거나 설치 캐시를 직접 편집하지 않습니다. 필요한 라이브러리나
호환 `rhwp`가 없으면 지원 가능한 순수 parser 결과와 coverage를 반환하거나 `runtime_unavailable`로 중단하며
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

별도 실행 파일은 `DOCUMENT_FILES_RHWP`로 지정할 수 있습니다. 공식 backend 준비 도구는 플랫폼별
체크섬과 macOS 서명을 확인하며 문서 처리 중 내려받지 않습니다. 일반 parser를 우선하고 `rhwp`는
HWP 보조 경로에만 사용합니다.

입력 파일과 출력 위치는 호출자가 소유합니다. 원본은 읽기 전용으로 다루고 변환·편집 결과는
별도 경로에 씁니다. 로컬 MCP와 host runtime은 현재 프로세스가 허용한 파일이나 전달된
바이트만 처리하며, 원본을 Toolkit 서버나 원격 Corpus에 저장하지 않습니다.

Sync는 변경되지 않는 캡처본을 로컬에서 분석하고 projection만 Corpus에 전달합니다.
실행 기능이 없으면 `runtime_unavailable`로 중단하며 다른 서버나 Cloudflare 분석기로
보내지 않습니다. AI 모델 전송은 아래의 명시적 연결 설정을 따릅니다.

## AI-assisted 스키마 추출

`extract-schema`와 `document_extract_schema`는 Document Files 내부에서 모델을 호출하여
스키마·시맨틱·값과 출처를 추출합니다. 일반 프로그램이 문서만 제출할 수 있으며, 호출자가
해석 결과를 작성해 다시 제출할 필요는 없습니다. 기존 `extract-structure`의 원본에 명시된
구조 추출 계약은 그대로 유지합니다.

로컬 CPU 실행에는 미리 설치한 runtime/model/recognition 팩을 관리자 프로필로 선택합니다.
`extract-schema input.docx --config server.json --profile cpu`는 같은 내부 엔진을 사용하며
문서 처리 중 모델을 내려받지 않습니다. 긴 작업은 HTTP 작업 제출·조회·취소·재개 경로를 사용할 수 있습니다.

별도 모델 연결은 실행 환경에서 명시적으로 설정합니다. HTTP 어댑터는 JSON 응답을 지원하는
Chat Completions 호환 로컬·클라우드 서버를 사용합니다. 다른 추론 방식은 Python의
`ModelClient`를 구현해 연결하며, 프롬프트·추가 읽기·재검사·완료 판단은 Document Files에
남습니다. 특정 제공자나 호스트 에이전트로 자동 연결하지 않습니다.

- `DOCUMENT_FILES_AI_ENDPOINT`: 실제 `/chat/completions` 요청 HTTP(S) URL. 로컬망 서버도 명시적으로 설정할 수 있습니다.
- `DOCUMENT_FILES_AI_MODEL`: 모델 식별자.
- `DOCUMENT_FILES_AI_API_KEY`: 필요한 경우 해당 서버의 인증 키. 결과에 저장하지 않습니다.

```sh
launchers/document-files extract-schema input.docx --request-id document-001
launchers/document-files get-extraction document-001 --section valueEvidence --limit 100
```

`--options options.json`으로 `intent`, `targetSchema`, `reconstructionContext`, `maxModelCalls`,
`contextChars`, `completionSeconds`, `maxInputBytes`를 지정합니다. MCP는 같은 입력 구조를
제공합니다. `targetSchema`가 없으면 문서에서 스키마를 발견합니다. 현재 검증기는 Draft
2020-12를 사용하되 외부 참조, 재귀 참조 및 정규식 조건을 지원하지 않습니다. 지원하지 않는
조건은 조용히 제거하지 않고 거절합니다.

결과는 `document-files.schema-extraction-result.v1`이며 `document`, `documentSchema`,
`dataSchema`, `semantics`, `data`, 양쪽 evidence, coverage와 validation을 포함합니다.
시맨틱의 대상·적용 범위는 `space`와 RFC 6901 `path`로 참조합니다. `document` 공간의
포인터는 `document.nodes`를 기준으로 합니다. 값의 원래 표기와 빈 값·부재·판독 실패·불확실성은
evidence에 남깁니다. 프로그램은 원문 참조·자료형·반복 매핑·적용 대상을 검사하고, 해결 가능한 문제를
내부 모델에 보완 요청합니다. 이미 유효한 결과를 훼손하는 보정은 수용하지 않습니다. `complete`는 관찰된 범위의 추출·검사 완료이며 모델의 정확성을
보증하는 표시는 아닙니다. `validation.semanticAccuracy`가 검사 방식을 나타냅니다.

CLI·MCP는 동기 실행합니다. 결과와 재개용 체크포인트는 기존 runtime 아래
`schema-extractions`에 사용자 전용 권한으로 보관되며 자동 만료하지 않습니다.
`--storage-dir` 또는 `DOCUMENT_FILES_STORAGE_DIR`로 저장 위치를 지정하고,
`extract-schema --no-retain`으로 보관하지 않을 수 있습니다. `delete-extraction`은
해당 결과와 체크포인트만 삭제하며 원문은 삭제하지 않습니다. 같은 요청 ID와 입력·옵션·모델 설정·프롬프트 버전은 저장 결과를
재사용하고, 다르면 거절합니다. 재분석에는 새 ID를 사용합니다. 실행 중에는 마지막으로 커밋된 부분 결과를 조회할 수 있습니다. SQLite 쓰기 트랜잭션은
모델 호출을 기다리지 않으며, 작업 소유 잠금으로 중복 실행을 방지합니다.
`resume-extraction`은 같은 입력·옵션·모델·프롬프트 버전인지 확인한 뒤 명시적으로
재개합니다. 이전 버전 결과는 계속 조회할 수 있으나 재개 정보가 없으면 재개하지 않습니다. 모델이 없으면
`partial`과 `ai_unavailable`을 반환합니다.

개인용 `reconstructionContext`는 기본 포함합니다. 텍스트의 원래 공백·개행과 Office/HWPX의
native XML·관계·스타일·이미지 등 패키지 구성요소를 자체 포함 형태로 보관합니다. 프로젝트용
추출에는 이 옵션을 끌 수 있습니다. PDF/HWP의 완전한 native 구성요소 추출과 수신 AI의 재현
시험은 별도 미완료 사항입니다. 구역별 해석·표 연결과 선택적 OCR은 구현했지만, 스캔 판독과
긴 문서 전체의 품질은 아직 출시 기준을 통과하지 않았습니다. 한 구역 자체가 문맥 한도를
넘거나 관계가 불확실하면 부분 결과로 처리합니다. 실제 모델·미지 문서의 품질 평가와
전체 설계 요구사항의 완성 여부는 플러그인 설치·업데이트와 구분합니다.


## 1.8.0 변경 내역 — 릴리스 후보

- 제품 소스·빌드·배포를 독립 저장소로 옮기고, Python·CLI·로컬 MCP와 클라이언트별
  배포 묶음을 같은 버전에서 생성하도록 구성했습니다.
- 원문 참조에서 값을 가져오는 바인딩과 스키마 필드의 근거 검사를 추가하고,
  숫자의 원래 표기·수식·저장된 계산값을 추출 결과에 보존하도록 개선했습니다.
- 중간 결과 보존·명시적 재개·결과 삭제·실행 환경 진단을 추가했습니다.
- Windows의 바이너리 입력·문자 인코딩·개인 저장 권한·하위 프로세스 정리를 보완하고,
  실행 환경과 rhwp 패치를 포함하는 플랫폼별 배포 절차를 마련했습니다.

위 항목은 구현 사항입니다. 실제 로컬·클라우드 모델의 의미 추출 품질, 각 설치형
클라이언트에서의 사용 검증과 정식 출시는 아직 완료되지 않았습니다. 플랫폼별 빌드·시험의
통과 여부는 해당 소스 커밋의 CI 결과로 확인하며 모델 품질 검증과 구분합니다.

## 1.7.0 업데이트

AI-assisted 추출·결과 조회와 함께 HWP→HWPX 체크형 글머리표 보존 검사를 포함합니다.
로컬에 준비된 `rhwp 0.8.6+pat.checkbox.1`을 우선 사용하며, 체크 문자·체크 상태·항목 순서와
줄 배치 속성을 원본과 대조합니다. 자체 빌드가 없는 환경에서도 검사는 유지하며, 손실을
확인하면 기본적으로 변환을 중단합니다.

자체 빌드는 Git과 Rust/Cargo를 준비한 뒤 다음 명령으로 만듭니다. 지정 경로는 아직 없어야
하며, 도구가 고정한 소스를 받아 [체크형 글머리표 패치](patches/rhwp/checkbox-preservation.patch)를
적용하고 사용자 캐시에 설치합니다. 공식 실행 파일은 별도로 보존합니다.

```bash
python3 scripts/build_patched_rhwp.py --source <새-로컬-클론-경로>
```

공식 빌드로 복귀할 때에는 해당 실행 파일을 `DOCUMENT_FILES_RHWP`로 지정하고 체크·미체크
혼합 문서의 문자·상태·순서, 불필요한 `keepLines` 여부와 손실 검사를 확인합니다. 버전이나
변경 기록만으로 복귀하지 않습니다. 검증 후 공식 버전·체크섬 고정값을 갱신하고 자체 빌드
우선 선택과 패치를 제거하되, 독립 보존 검사는 유지합니다. 실제 사용 runtime이 공식
빌드를 선택하는지 확인한 뒤에만 로컬 클론과 자체 빌드 실행 파일을 지웁니다.

## Python API와 작업 관리

```python
from document_files.api import extract_schema, extraction_result_schema

result = extract_schema(
    "input.docx",
    options={"reconstructionContext": False},
    retain=False,
)
# dataSchema, semantics, data, evidence, coverage와 issues를 함께 사용합니다.
# result["extraction"]["status"]가 partial이면 완전 추출로 취급하지 않습니다.
```

경로 없이 연동할 때는 `AnalysisJob`과 byte stream을 받는
`document_files.api.extract_schema_from_stream`을 사용합니다.
`document_files.api.extraction_result_schema()`가 공개 결과 JSON Schema를 반환합니다.
기존 분석 v1과 추출 결과 v1을 유지하며 내부 모델의 해석안을 호출자가 만들 필요가 없습니다.

`completionSeconds`는 현재 실행의 응답 수락과 완료 판정에 적용하는 총 시간 예산입니다.
모델 호출에는 남은 시간을 전달하며, 늦은 응답을 완전 추출로 채택하지 않습니다.
느린 소켓이나 외부 모델 프로세스의 강제 종료를 보장하는 설정은 아닙니다.

```sh
document-files diagnose
document-files extract-schema input.docx --request-id example --storage-dir /private/results
document-files get-extraction example --storage-dir /private/results
document-files resume-extraction example --storage-dir /private/results
document-files delete-extraction example --storage-dir /private/results
```

`DOCUMENT_FILES_AI_RESPONSE_FORMAT=none`은 JSON 응답 모드를 별도로 지원하지 않는
호환 서버에 사용합니다. 응답 본문의 JSON·자료형 검사는 계속 수행합니다.
`DOCUMENT_FILES_AI_MAX_OUTPUT_TOKENS`로 응답 토큰 상한을 설정할 수 있습니다.
인증 실패·요청 제한·시간 초과와 구성 오류는 구분되며, 응답 원문이나 키를 오류에 넣지 않습니다.

AI는 원문의 어느 부분이 어떤 필드·반복 영역에 해당하는지 해석합니다. 명시적인 값에는
원문 binding을 적용해 실제 값을 구성하며, 원래 표기와 변환을 evidence에 보존합니다.
스키마의 필드 정의에도 출처가 필요합니다. `complete`는 선언된 관측 범위에서의 실행·검사
완료이지, 모든 문서에 대한 의미 정확성 인증이 아닙니다.

## 개발 검증과 추후 릴리스

일상 작업은 `main`에서 직접 진행하며, 변경과 관련된 기존 시험을 먼저 실행합니다.
GitHub Actions는 모두 수동 실행으로 바꾸었습니다. 커밋·PR·주기 실행으로 빌드를 시작하지
않으며, 필요한 경우 대상 하나를 골라 확인합니다. 기본 대상은 Linux ARM64입니다.
다른 플랫폼의 빌더와 보안·라이선스 검사는 추후 사용을 위해 유지합니다.

[모델 평가](evaluation/README.md)는 개발 사례와 독립 판정을 구분합니다. 입력·소스·팩·결과를
고정하고 원문 및 사전 정답과 실제 출력을 대조합니다. 정확성 확인과 한도 초과 시 부분 결과
확인은 서로 다른 검사입니다. 모델 재실행과 설치 전환은 각각 필요할 때 명시적으로 진행합니다.

정식 배포를 다시 진행할 때에는 [릴리스 절차](deployment/RELEASE.md)에 따라 대상과 증거를
확정하고 동일한 검증 파일만 공개합니다. 현재 미완료 검사를 통과한 것으로 바꾸거나,
Spark 개발 성공을 기존 CPU·다중 플랫폼 출시 검사에 대신 넣지 않습니다.

이관 전 1.7.0의 제품 이력은 Toolkit의 `plugins/document-files` 경로에서 분리했으며,
출발 커밋은 `ff7bdf88aa5ea85988bedc86defb872c7487a72c`입니다.
Toolkit의 기존 Git 이력은 수정하지 않았습니다. Toolkit 소비 전환은 독립 릴리스와
소비 측 검증 후 활성화하며, 그전의 원본 경로는 수정하지 않는 호환 기준으로만 유지합니다.
