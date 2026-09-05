---
name: presentations
description: PPTX 또는 Google Slides 프레젠테이션을 만들거나 슬라이드, 표, 차트, 이미지와 발표자 노트를 편집할 때 사용한다. 로컬 파일의 단순 추출은 document-files를 사용한다.
---

# Presentations

## PPTX 제작과 편집

- 호스트가 제공하면 `artifact-tool`을 우선하고, 필요한 기능이 없을 때 `python-pptx`를 사용한다.
- 기존 자료는 원본을 보존하고 별도 PPTX로 저장한다. master, layout, 관계와 미디어를 요청 범위 밖에서 바꾸지 않는다.
- 편집 가능한 표·차트·도형이 요청된 경우 해당 요소를 실제 편집 가능한 개체로 만든다. 구현 편의 때문에 이미지로 대체하지 않는다.
- 편집 뒤 파일을 다시 열어 슬라이드 수와 순서, 요청한 텍스트·표·차트 데이터·노트, 패키지 관계를 확인한다.
- LibreOffice나 PowerPoint 앱 제어, 의무적 전체 슬라이드 렌더링과 snapshot 비교를 완료 조건으로 두지 않는다. 시각 확인이 필요한 경우 요청된 슬라이드만 보조 확인한다.
- 실행 라이브러리가 없으면 `runtime_unavailable`로 중단하고 다른 원격 문서 처리 서버로 보내지 않는다.

## Google Slides 요청

- 기존 Google Slides는 해당 프레젠테이션 ID를 대상으로 현재 연결 도구로 직접 편집한다. 요청 없이 PPTX로 왕복 변환하거나 새 파일로 대체하지 않는다.
- 새 Google Slides가 필요하면 로컬 PPTX를 만든 뒤, 제공되는 `google_drive_import_presentation`의 `native_google_slides` 모드로 변환한다. `source_file`에는 실행 호스트에서 읽을 수 있는 파일의 절대경로를 전달한다.
- 반환된 `presentationId`와 Google Slides MIME 유형을 확인하고, 해당 프레젠테이션을 다시 읽어 요청한 슬라이드·내용·편집 가능 요소가 반영됐는지 확인한다. PPTX 저장이나 `keep_source_file_type` 업로드만으로 Google Slides 제작을 완료했다고 하지 않는다.
- 이 경로는 Google Slides 결과물을 요청받은 경우에만 사용한다. 로컬 PPTX 제작 요청을 업로드·공유·공개 승인으로 확대하지 않으며, 필요한 도구나 권한이 없으면 그 범위를 알린다.
