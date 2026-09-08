# Document Files

Document Files는 문서의 구조·의미·값을 추출하는 독립 Python 제품입니다. 내부 AI가
해석·추가 읽기·보완·검사를 수행하고, 프로그램과 에이전트는 같은 Python API·CLI·MCP를
사용합니다. 기존 문서 읽기·변환·HWPX 제작/편집과 단일 문서 Skill도 함께 제공합니다.

소스 정본은 [Ruzzy77/document-files](https://github.com/Ruzzy77/document-files)입니다.
Personal Agent Toolkit과 Sync는 이 제품의 지정 릴리스를 소비합니다. 개인 데이터나
회사별 양식은 제품 저장소에 넣지 않습니다.

## 1.8.0 상태와 설치

현재 1.8.0은 독립 제품 전환의 **릴리스 후보**입니다. 실제 로컬·클라우드 모델 및 각
클라이언트의 검증이 끝나기 전에는 정식 지원 품질이 확인됐다고 표시하지 않습니다.
코드의 기능, 설치 가능 여부, 모델별 의미 추출 품질은 각각 확인합니다.

- 개발·Python 연동: `uv sync --frozen` 또는 배포된 wheel을 설치합니다.
- 일반 사용자: GitHub Releases의 운영체제별 실행 환경 포함 묶음을 사용합니다.
  Python·uv를 별도로 설치할 필요가 없으며 `launchers/document-files`
  (Windows는 `document-files.cmd`)로 실행합니다. 모델 서버·모델 가중치는 포함하지 않습니다.
- 연결 배포: Codex·Claude Code 플러그인, Claude Desktop `.mcpb`, ChatGPT `.skill`을
  같은 소스 버전에서 생성합니다. ChatGPT는 호스트가 허용하는 실행 환경과 연결을 사용합니다.
- 준비 대상: macOS arm64/x64, Windows x64, Linux x64. 실제 검증 결과와 제약이 없는
  플랫폼을 지원 완료로 표시하지 않습니다. OS 보안 설정을 해제하는 설치 방법은 제공하지 않습니다.

첫 정식 AI 추출의 검증 대상은 TXT·Markdown·HTML·DOCX·HWPX·XLSX의 텍스트·명시적
구조 중심 문서입니다. PDF·HWP·PPTX의 기존 읽기와 변환은 유지하지만, 시각 정보나
긴 문서 통합이 필요한 AI 추출은 부분 결과로 남습니다. 새로운 양식과 새로운 파일 형식의
지원을 구분하며, 모든 미지 문서에 대한 무오류 보장을 하지 않습니다.

빌드와 품질 검증은 아래 [릴리스 검증](#릴리스-검증)을 따릅니다.

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

Corpus의 기존 로컬 연동은 원본 경로 대신 상속한 읽기 전용 파일 descriptor를 사용하는 엄격한 JSONL 경계를 유지합니다. 이 경계는 기존 호출자를 위한 로컬 전송 방식이며, 문서 분석 계약 자체의 입력 형식은 아닙니다.

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

실행 경계와 데이터 소유 원칙은 [DESIGN.md](./DESIGN.md)에 있습니다.

## AI-assisted 스키마 추출

`extract-schema`와 `document_extract_schema`는 Document Files 내부에서 모델을 호출하여
스키마·시맨틱·값과 출처를 추출합니다. 일반 프로그램이 문서만 제출할 수 있으며, 호출자가
해석 결과를 작성해 다시 제출할 필요는 없습니다. 기존 `extract-structure`의 원본에 명시된
구조 추출 계약은 그대로 유지합니다.

모델 연결은 실행 환경에서 명시적으로 설정합니다. 현재 HTTP 어댑터는 JSON 응답을 지원하는
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
evidence에 남깁니다. 원본·참조·자료형 검사를 통과하면 별도 AI 대조를 수행하며, 발견된 문제가
있으면 내부에서 수정합니다. `complete`는 관찰된 범위의 추출·검사 완료이며 모델의 정확성을
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
추출에는 이 옵션을 끌 수 있습니다. PDF/HWP의 완전한 native 구성요소 추출, 시각 정보를 통한
내부 AI 판독, 긴 문서의 분할 후 통합, 수신 AI의 재현 시험은 아직 완료하지 않았습니다.
모델 문맥 한도를 넘으면 부분 결과로 처리합니다. 실제 모델·미지 문서의 품질 평가와
전체 설계 요구사항의 완성 여부는 플러그인 설치·업데이트와 구분합니다.


## 1.7.0 업데이트

AI-assisted 추출·결과 조회와 함께 HWP→HWPX 체크형 글머리표 보존 검사를 포함합니다.
로컬에 준비된 `rhwp 0.8.6+pat.checkbox.1`을 우선 사용하며, 체크 문자·체크 상태·항목 순서와
줄 배치 속성을 원본과 대조합니다. 자체 빌드가 없는 환경에서도 검사는 유지하며, 손실을
확인하면 기본적으로 변환을 중단합니다. 자체 빌드 준비와 공식 버전 복귀 조건은
[DESIGN.md](DESIGN.md#rhwp-체크형-글머리표-보존과-공식-빌드-복귀)에 있습니다.

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

## 릴리스 검증

제품 전용 CI는 네 운영체제/아키텍처에서 기존 회귀시험과 실행 환경 포함 배포본을 검사합니다.
Python runtime·의존성·rhwp는 빌드 시 고정하고 체크섬 및 라이선스를 함께 배포합니다.
문서 처리 중 provisioning이나 자동 원격 분석 폴백은 없습니다.

[실제 모델 평가](evaluation/README.md)는 개발 예제와 별도로 준비한 공개 holdout을
사용하며, 평가 결과의 source commit·모델·입력·결과 해시를 기록합니다.
실제 클라이언트의 문서 실행과 모델 검증 근거가 모두 있어야
`scripts/check_release_qualification.py`가 정식 릴리스를 허용합니다.
CI 성공이나 모델 자체 검토만으로 그 근거를 대체하지 않습니다.

이관 전 1.7.0의 제품 이력은 Toolkit의 `plugins/document-files` 경로에서 분리했으며,
출발 커밋은 `ff7bdf88aa5ea85988bedc86defb872c7487a72c`입니다.
Toolkit의 기존 Git 이력은 수정하지 않았습니다. Toolkit 소비 전환은 독립 릴리스와
소비 측 검증 후 활성화하며, 그전의 원본 경로는 수정하지 않는 호환 기준으로만 유지합니다.
