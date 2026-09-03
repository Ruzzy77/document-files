# Document Files 설계

## 목적과 경계

Document Files는 호출자가 허용한 로컬 문서 바이트를 읽고 구조·본문·coverage를 추출하며, 지원
형식을 변환·렌더링하고 HWPX 산출물을 만들고 검증한다. 원본과 사용자 데이터를 소유하거나 원격
서비스에 보관하지 않는다.

## 구성과 실행

plugin 안의 Python package가 형식 판별, parser adapter, 공통 분석 contract와 artifact 작업을
구현한다. CLI와 local MCP는 같은 application 함수를 사용한다. Corpus는 문서 등록·정책·검색을
맡고 Document Files에는 승인된 바이트와 분석 작업만 전달한다.

형식별 구현은 공통 결과에서 원본 구조, 의미 역할, coverage와 issue를 구분한다. adapter의 정확한
version과 설정은 결과 재현 정보이며, 내용이 같은 문서의 재분석 여부를 자동으로 결정하지 않는다.

## 데이터와 권한

입력 파일과 출력 위치는 호출자가 소유한다. 원본은 읽기 전용으로 다루고 변환·편집 결과는 별도
경로에 쓴다. local MCP는 현재 프로세스와 운영체제가 허용한 파일에만 접근하며, 원격 MCP나 사용자
데이터베이스를 만들지 않는다.

## 공개 표면

공개 local MCP는 capability 확인, 검사, 텍스트·구조 추출, 변환, HWPX 생성·편집·검증과 렌더링을
제공한다. 정확한 도구 목록과 version은 루트 [`products.json`](../../products.json)이 정본이다.
CLI는 사람이 직접 다루는 작업과 Corpus analyzer transport를 제공하지만 별도 제품 계약을 만들지
않는다.

## 버전과 검증

manifest, Python package와 lockfile은 같은 base version을 사용한다. parser나 편집 로직을 바꿀
때에는 관련 형식의 집중 테스트와 생성물 구조 검증을 수행한다. 화면 충실도가 중요한 결과만
렌더링해 확인하며, headless 결과를 네이티브 앱 검증으로 간주하지 않는다.
