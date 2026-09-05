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

후속 구현에서는 `inspect`·텍스트 추출과 공통 구조 추출이 서로 다른 backend를 요구하던 문제를
정리했다. 순수 분석 파서를 우선하는 읽기와 편집·변환·미리보기의 실행 조건을 구분하며, 분석
계약과 재분석 세대는 유지했다.

세 읽기 명령은 호출마다 한 번의 `AnalysisJob` 결과를 사용한다. `inspect`는
파일과 추출 범위를 요약하고, 텍스트·Markdown은 같은 결과의 본문을, 구조 추출은 위치·값·관계를
제공한다. 추출의 완전성과 출력 길이로 인한 잘림을 구분하고 Markdown을 원본 조판의 복원으로
표시하지 않는다. 표 셀의 구조 단위와 그 안의 문단을 중복 출력하지 않되 다른 셀의 같은 값은
보존한다. HWPX 편집에는 원본 XML의 section과 표 순서에 대응하는 검증된 선택자를 제공하며,
단순 읽기에 편집 라이브러리를 요구하지 않는다.

실제 한국어 HWP와 손실 검사 후 로컬에서 변환한 HWPX 한 사례를 편집 도구와 `rhwp`가 없는
호스트에서 읽었다. 기존 구조 추출의 본문·unit·issue를 그대로 유지했으며, 표 58개 셀의 값·좌표와
병합 범위를 다른 backend 결과와 대조했다. HWPX에서는 다중 문단 셀 하나를 선택해 편집하고
재열었으며 나머지 57개 셀의 값과 표 구조, 입력 원본의 바이트가 유지되는 것을 확인했다.

이 과정에서 편집기의 `originalText`가 첫 문단만 보고한다는 점과, 기존 문단보다 많은 줄을
요청하면 일부 텍스트가 빠져도 성공을 반환하는 문제를 확인했다. `expectedOldText`가 있으면 이미
읽은 동일 바이트를 한 번 분석해 셀 전체와 대조한다. 원본 값과 같은 요청은 바이트를 보존하고,
같은 물리 셀의 중복 지정과 기존 문단으로 표현할 수 없는 새 줄은 저장 전에 거절한다. 중첩 표를
포함하는 바깥 셀 편집도 내부 값 손실이 재현돼 거절하며, 중첩 표 안의 일반 셀 편집은 유지한다.
이 확인은 고정된 편집기 버전의 위치 함수에 의존하므로 버전 변경 때 함께 확인한다.

이 사례와 기존 fixture 확인은 본문·값·구조의 근거이며 기관 양식의 조판·넘침·긴 표 보존까지
입증하지는 않는다. `verify.ok`는 패키지·요청 텍스트 검사 결과이고, reference와의 표 구조 비교는
`comparison.tableGeometryPreserved`에서 별도로 확인한다. 호스트 기본 기능과의 실제 품질·소요
시간 비교, 지원 범위 축소는 아직 결정하지 않았다. 문서·Sync 수정과 문서 Skill 정리를 한 묶음으로
반영하며, 클라이언트 설치·현재 세션의 사용 확인 전에는 release 완료로 보지 않는다.
