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
