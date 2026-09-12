# Document Files

PDF, Office, HWP/HWPX 문서를 읽고 만들거나 편집하는 AI 에이전트용 플러그인입니다.
본문과 표를 추출하고, 문서에 기록된 구조와 값을 원본 위치가 연결된 JSON으로 제공합니다.
문서 작업 방법을 담은 `Document Files` Skill과 로컬 실행 도구를 함께 제공하며,
Python API, CLI, MCP, HTTP를 통해 다른 프로그램에서도 사용할 수 있습니다.

## 주요 기능

### 문서 읽기와 구조 추출

**PDF, DOCX, PPTX, XLSX, HWP, HWPX, HTML, Markdown, TXT**를 읽습니다.

- 본문을 텍스트나 Markdown으로 추출합니다.
- 제목, 문단, 목록, 표, 병합 셀과 셀 좌표를 구조화된 데이터로 반환합니다.
- XLSX의 값과 자료형, 수식, 저장된 계산값을 구분합니다. 수식을 새로 실행하지는 않습니다.
- 추출한 항목의 원본 위치와 읽지 못한 범위를 함께 제공합니다. 큰 구조 결과는 나눠 조회할 수 있습니다.

일반 읽기와 원본 구조 추출에는 AI 모델이 필요하지 않습니다. 이미지로만 된 페이지에서는
텍스트를 얻지 못할 수 있습니다. 스캔 PDF 인식과 추가 시각 판독은 아래 AI 추출 경로에서
별도의 인식 팩과 호환 vision 모델 구성을 사용합니다.

### 문서 작성과 편집

`Document Files` Skill은 에이전트가 호스트의 라이브러리와 연결 도구를 사용해 다음 작업을
수행하도록 안내합니다. 사용 가능한 기능은 해당 환경에 준비된 도구에 따라 달라집니다.

| 문서 | 작업 |
| --- | --- |
| DOCX | 본문과 표가 있는 Word 문서 작성·편집 |
| XLSX·CSV | 표 데이터 작성·편집, 수식 분석 |
| PPTX | 편집 가능한 슬라이드와 표 작성·편집 |
| PDF | PDF 제작, 페이지 조작, 양식 편집 |
| Google Docs·Sheets·Slides | 연결된 Google 도구를 통한 읽기·작성·편집 |

이 작업들은 형식별 [Skill 안내](skills/document-files/SKILL.md)를 사용합니다.
일반 Office 문서 제작과 열린 Office 앱의 직접 제어는 로컬 분석기의 MCP 명령에 포함되지 않습니다.

### HWP와 HWPX

로컬 실행 도구는 HWP/HWPX의 읽기 외에 다음 기능을 제공합니다.

- HWP를 HWPX로 변환하고, 체크형 글머리표의 문자와 체크 상태 등 변환 손실을 검사합니다.
- JSON 작성 계획으로 HWPX를 만들고, 기존 HWPX의 문구와 표 셀을 별도 파일에 편집합니다.
- HWPX를 다시 열어 패키지와 요청한 내용, 원본 대비 표 구조를 검사합니다.
- HWP/HWPX의 SVG·PDF 미리보기와 HWPX의 HTML 미리보기를 만듭니다.

생성·편집에는 HWPX 라이브러리, HWP 변환과 SVG·PDF 미리보기에는 호환 `rhwp`가 필요합니다.
미리보기의 조판은 원본 앱 화면과 다를 수 있습니다. HWP 원본 편집, HWPX→HWP 변환과
암호·문서 보호 우회는 지원하지 않습니다.

### AI 스키마 추출 — 개발 기능

AI 모델을 연결하면 문서의 필드와 반복 구조를 해석해 스키마, 값, 단위·조건·주석의 관계와
출처를 반환합니다. 호출자는 문서와 추출 조건을 전달하고, Document Files가 내부 모델 호출과
결과 검사를 수행합니다. 원본에 명시된 구조를 읽는 `extract-structure`와 구분되는 기능입니다.

- 문서에서 스키마를 찾거나, 지정한 `targetSchema`에 맞춰 추출합니다.
- `dataSchema`, `data`, `semantics`와 함께 필드·값의 출처, 불확실성, 미완료 항목을 제공합니다.
- 결과 저장·조회·삭제와 명시적 재개를 지원합니다. 관리형 작업은 상태 조회와 취소도 지원합니다.
- 준비된 로컬 CPU/CUDA 팩 또는 명시적으로 설정한 Chat Completions 호환 모델 서버를 사용합니다.

**AI 추출은 정확성 검증이 진행 중입니다.** 복잡한 표의 행 보존과 중복 판단 등 알려진 문제가
남아 있으므로 중요한 업무에 적용하기 전에는 원문과 대조해야 합니다. 자세한 제약은
[지원 범위](SUPPORT.md)를 확인해 주세요. 모델이 설정되지 않으면 AI 추출의 사용 불가를 알리며,
호스트의 대화 모델이나 다른 서버에 자동으로 연결하지 않습니다.

## 시작하기

현재 소스는 **1.8.0 개발 버전**입니다. 정식 GitHub 릴리스는 아직 없으며, 소스에서 실행할 수 있습니다.
아래 예시는 `uv`와 Python 3.12를 사용합니다.

```sh
git clone https://github.com/Ruzzy77/document-files.git
cd document-files
uv sync --frozen --python 3.12
launchers/document-files capabilities
```

`capabilities`에서 해당 환경의 읽기·편집·변환 기능과 필요한 백엔드의 준비 상태를 확인할 수
있습니다. AI 모델과 인식 팩은 별도로 준비합니다. 팩 설치와 모델 프로필 설정은
[설치 안내](deployment/README.md)와 [운영 안내](docs/operations.md)를 참고해 주세요.

### CLI 사용 예

```sh
# 파일과 추출 범위 확인
launchers/document-files inspect input.pdf

# 본문 읽기
launchers/document-files extract input.docx --format text

# 표, 셀 좌표와 값 읽기
launchers/document-files extract-structure input.xlsx --max-units 500

# HWP를 별도 HWPX 파일로 변환 — 호환 rhwp 필요
launchers/document-files convert input.hwp output.hwpx
```

AI 추출은 준비한 관리자 프로필을 지정해 실행합니다. 다음의 `server.json`과 `gpu`는
실제 설치한 팩에 맞춰 구성한 파일과 프로필 이름입니다.

```sh
launchers/document-files extract-schema input.pdf \
  --config server.json --profile gpu --request-id document-001
launchers/document-files get-extraction document-001
```

### 에이전트와 프로그램에서 사용

MCP 클라이언트에는 준비된 저장소의 `launchers/document-files-mcp`를 서버 실행 명령으로
등록합니다. 에이전트에는 문서 작업 방법을 담은 [Document Files Skill](skills/document-files/SKILL.md)을
함께 연결할 수 있습니다. 호스트가 제공하는 파일 접근과 실행 권한 안에서 동작합니다.

Python에서는 `document_files.api`를 사용합니다. HTTP 서비스는 문서 바이트를 업로드하고
작업 상태와 결과를 조회하는 방식을 제공합니다. 호출 예제, 모델 설정, 결과 형식과 작업 관리
명령은 [Python·CLI·MCP 연동 문서](docs/python-api.md)에 있습니다.

## 원본과 데이터 처리

원본 파일은 읽기 전용으로 다루고, 생성·변환·편집 결과는 별도 경로에 저장합니다.
일반 파일 처리는 로컬에서 수행합니다. AI 추출에 외부 모델 서버를 설정하면 필요한 원문이
그 서버로 전달되며, Google 문서 작업은 사용자가 연결한 계정과 권한을 사용합니다.
문서 처리 중 모델이나 의존성을 자동으로 내려받지 않습니다.

추출 결과에는 읽지 못한 내용과 불확실성을 표시합니다. AI 추출의 `complete`는 실행과 내부
검사가 끝났다는 뜻이며 의미 정확성을 보증하지 않습니다. 저장 결과를 삭제해도 호출자가
보관한 원본은 삭제하지 않습니다.

## 문서

- [Python·CLI·MCP API](docs/python-api.md)
- [팩 설치](deployment/README.md)와 [서버 운영](docs/operations.md)
- [지원 범위와 알려진 제약](SUPPORT.md)
- [제품 구조](docs/product-architecture.md)와 [추출 엔진 상세](docs/extraction-engine.md)
- [개발 안내](CONTRIBUTING.md), [변경 내역](CHANGELOG.md), [보안 안내](SECURITY.md)

## 라이선스

Document Files 엔진은 [Apache-2.0](LICENSE)으로 제공됩니다. 저작권 고지는 [NOTICE](NOTICE)를
참고해 주세요. 함께 사용하는 라이브러리, 실행 파일과 모델에는 각각의 라이선스가 적용됩니다.
