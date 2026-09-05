# Google 문서

Google Docs·Sheets·Slides 자체의 읽기·제작·편집이 요청됐을 때 적용한다. 로컬 파일 작업을 업로드·공유·공개 요청으로 확대하지 않는다.

## 기존 문서

현재 연결 도구로 지정된 ID의 문서를 읽고 직접 편집한다. 요청 없이 Office 파일로 왕복 변환하거나 새 문서로 대체하지 않는다. 필요한 연결이나 권한이 없으면 해당 범위를 알리고, 로컬 복사본으로 대신 완료했다고 하지 않는다.

## 새 문서

직접 생성 기능이 있으면 현재 도구 계약을 따른다. 호스트 파일을 가져오는 경로를 사용한다면 요청 형식에 해당하는 내부 안내로 로컬 파일을 만든 뒤, 아래 도구가 제공되는지 확인한다.

| 결과 | 로컬 원본 | 가져오기 도구 | 변환 모드 | 반환 ID |
| --- | --- | --- | --- | --- |
| Google Docs | [DOCX](docx.md) 또는 HTML | `google_drive_import_document` | `native_google_docs` | `documentId` |
| Google Sheets | [XLSX](workbooks.md) | `google_drive_import_spreadsheet` | `native_google_sheets` | `spreadsheetId` |
| Google Slides | [PPTX](slides.md) | `google_drive_import_presentation` | `native_google_slides` | `presentationId` |

`source_file`에는 실행 호스트에서 읽을 수 있는 파일의 절대경로를 전달한다. 반환 ID와 해당 Google 문서 MIME 유형을 확인하고 같은 문서를 다시 읽는다. Docs는 요청한 본문·표, Sheets는 시트·범위·값·수식, Slides는 슬라이드·내용·편집 가능한 요소가 반영됐는지 확인한다.

로컬 파일 저장이나 `keep_source_file_type` 업로드만으로 Google 문서 제작을 완료했다고 하지 않는다.
