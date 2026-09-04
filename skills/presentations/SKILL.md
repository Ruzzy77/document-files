---
name: presentations
description: PPTX 프레젠테이션을 새로 만들거나 슬라이드, 텍스트, 표, 차트, 이미지와 발표자 노트를 편집할 때 사용한다. 단순 추출은 document-files를 사용한다.
---

# Presentations

- 호스트가 제공하면 `artifact-tool`을 우선하고, 필요한 기능이 없을 때 `python-pptx`를 사용한다.
- 기존 자료는 원본을 보존하고 별도 PPTX로 저장한다. master, layout, 관계와 미디어를 요청 범위 밖에서 바꾸지 않는다.
- 편집 뒤 파일을 다시 열어 슬라이드 수와 순서, 요청한 텍스트·표·차트 데이터·노트, 패키지 관계를 확인한다.
- LibreOffice나 PowerPoint 앱 제어, 의무적 전체 슬라이드 렌더링과 snapshot 비교를 완료 조건으로 두지 않는다. 시각 확인이 필요한 경우 요청된 슬라이드만 보조 확인한다.
- 실행 라이브러리가 없으면 `runtime_unavailable`로 중단하고 다른 원격 문서 처리 서버로 보내지 않는다.
