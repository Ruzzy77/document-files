# Document Files 설계

## 목적과 경계

Document Files는 호출자가 허용한 문서 바이트에서 구조·본문·coverage를 추출하고, 실행 가능한
호스트에서 지원 형식을 변환하며 HWPX 산출물을 만들고 검증한다. 원본과 사용자 데이터를 소유하거나
Personal Agent Toolkit 서버와 Corpus 원격 저장층에 보관하지 않는다.

## 구성과 실행

plugin 안의 Python package가 형식 판별, parser adapter, 공통 분석 contract와 HWPX artifact 작업을
구현하는 단일 정본이다. CLI와 Claude local MCP는 같은 application 함수를 사용하고, Sync는 같은
package를 자기 환경에 설치한다. OpenAI 통합 plugin과 개인 ChatGPT용 Personal Skills는 이 소스에서
만든 host 실행 번들과 다섯 문서 Skill을 사용한다. `AnalysisJob v1`·`AnalysisResult v1` 계약은 모든
실행 위치에서 동일하다.

형식별 구현은 공통 결과에서 원본 구조, 의미 역할, coverage와 issue를 구분한다. adapter의 정확한
version과 설정은 결과 재현 정보이며, 내용이 같은 문서의 재분석 여부를 자동으로 결정하지 않는다.

## 데이터와 권한

입력 파일과 출력 위치는 호출자가 소유한다. 원본은 읽기 전용으로 다루고 변환·편집 결과는 별도
경로에 쓴다. 로컬 MCP와 host runtime은 현재 프로세스가 허용한 파일이나 전달된 바이트에만 접근한다.
Sync는 immutable capture를 로컬에서 분석하고 projection만 Corpus에 전달한다. 실행 기능이 없으면
`runtime_unavailable`로 중단하며 다른 서버나 Cloudflare 분석기로 보내지 않는다.

## 공개 표면

Claude의 공개 local MCP는 capability 확인, 검사, 텍스트·구조 추출, 변환, HWPX 생성·편집·검증과
보조 렌더링을 제공한다. 모든 도구는 구체적인 Pydantic `outputSchema`와 `{ok, result, error}` 응답을
사용한다. OpenAI에서는 별도 Document Files MCP나 Codex plugin 없이 통합 plugin의 Skill과 host
runtime을 쓴다. 개인 ChatGPT에서는 같은 정본에서 만든 Personal Skill archive를 쓴다. 정확한 도구
목록과 version은 루트 [`products.json`](../../products.json)이 정본이다.

## 버전과 검증

Claude manifest, Python package와 lockfile은 같은 base version을 사용한다. OpenAI 통합본은 원본
package와 Skill에서 생성하며 복사본을 정본으로 편집하지 않는다. parser나 편집 로직을 바꿀 때에는
형식군별 대표 fixture와 생성물 재열기만 검증하고 의무적 이미지 snapshot이나 별도 보고서를 만들지
않는다. 화면 렌더링은 보조 확인이며 구조·값 검증을 대신하지 않는다. `rhwp`는 준비 단계에서만
서명·체크섬을 확인해 설치하고 문서 처리 중 자동 다운로드하지 않는다.

## 개선 범위와 순서

전면 검토의 범위 04에서는 HWP/HWPX의 원본 위치·표·병합 셀·각주·필드 추출과 손실을 알리는
변환, 원본을 보존하는 HWPX 편집을 직접 유지한다. 공통 분석 계약과 XLSX의 저장값·수식·캐시
구분도 유지한다. 일반 DOCX·PPTX·XLSX·PDF 제작은 호스트 라이브러리에 맡기고, 문서 Skill은
작업 분담과 보존·완료 기준을 제공한다. 자체 제작 엔진을 추가하지 않는다.

첫 구현에서는 기존 기능을 사용할 때 발생하는 두 결함을 복구했다. 수정된 runtime은 연관 지침
개편과 함께 Codex 통합본에 포함했다. Claude·개인 ChatGPT 설치 반영은 후속 읽기 경로 정리와
같은 Document Files 반영 묶음에서 진행한다.

1. 개인 ChatGPT용 Skill 생성 과정에서 셸 진입점의 경로와 실제 호출을 일치시켰다. 생성된
   archive를 풀어 그 안에 적힌 호출로 capability 조회와 한국어 구조 추출을 확인했다.
2. HWP/HWPX `inspect` 결과가 Claude local MCP의 공개 응답 계약을 만족하도록 했다. 기존
   `file` 등 형식별 정보와 메타데이터만 읽은 경우의 `contentAccess` 제한은 보존했다. 전체 문서
   시험과 실제 MCP 호출에서 정상 응답을 확인했다.

그다음 `inspect`·텍스트 추출과 공통 구조 추출이 서로 다른 backend를 요구하는 문제를 정리한다.
순수 분석 파서를 우선하는 읽기와 편집·변환·미리보기의 실행 조건을 구분하며, 이번 응답·배포
복구만으로 분석 계약이나 재분석 세대를 바꾸지 않는다.

기존 합성 fixture와 생성물 재열기 확인은 추출 관계와 파일 구조의 근거이며, 실제 기관 양식의
조판·넘침·긴 표 보존까지 입증하지는 않는다. 읽기 경로를 정리한 뒤 한국어 업무 문서 한 사례에서
필요한 내용·값·표와 사용 가능한 출력이 보존되는지 확인한다. `verify`의 정보성 비교와 완료 판정도
그때 구분한다. 호스트 기본 기능과의 실제 품질·소요 시간 비교, 지원 범위 축소는 아직 결정하지 않았다.
