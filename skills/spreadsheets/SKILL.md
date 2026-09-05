---
name: spreadsheets
description: XLSX·CSV 또는 Google Sheets를 만들거나 편집하고 표, 셀 값, 형식과 수식을 분석할 때 사용한다. 열린 Excel 앱 제어나 로컬 문서의 단순 읽기에는 사용하지 않는다.
---

# Spreadsheets

## XLSX·CSV 제작과 편집

- XLSX는 호스트가 제공하면 `artifact-tool`을 우선하고, 필요한 기능이 없을 때 `openpyxl`을 사용한다. CSV는 표준 `csv` 모듈 같은 로컬 텍스트 처리 기능으로 직접 수정하며, 원래의 열 구분·인코딩·문자열 값을 보존한다.
- 열린 Excel 앱은 제어하지 않는다. 입력 파일을 직접 덮어쓰지 말고 별도 출력으로 저장한다.
- 저장된 값의 자료형, 수식과 계산 캐시를 구분한다. 실제 계산 엔진을 실행하지 않고 수식 결과를 임의로 만들거나 캐시를 최신 결과로 단정하지 않는다.
- 요청 범위 밖의 수식, 주석과 데이터 유효성 검사 설정을 보존한다.
- XLSX 편집 뒤 워크북을 다시 열어 요청한 시트, 셀 좌표, 값, 수식, 병합 범위와 주요 형식이 보존됐는지 확인한다. CSV는 같은 인코딩과 구분자로 다시 읽어 요청한 행·열과 값을 확인하며, 검사를 위해 XLSX로 변환하지 않는다.
- 전체 셀 덤프, 화면 snapshot, 별도 회귀 보고서는 요구하지 않는다. 요청과 관련된 범위만 확인한다.
- 실행 기능이 없으면 `runtime_unavailable`로 중단하며 원문을 별도 원격 분석기로 전송하지 않는다.

## Google Sheets 요청

- 기존 Google Sheets는 해당 스프레드시트 ID를 대상으로 현재 연결 도구로 직접 편집한다. 요청 없이 XLSX로 왕복 변환하거나 새 파일로 대체하지 않는다.
- 새 Google Sheets가 필요하면 로컬 XLSX를 만든 뒤, 제공되는 `google_drive_import_spreadsheet`의 `native_google_sheets` 모드로 변환한다. `source_file`에는 실행 호스트에서 읽을 수 있는 파일의 절대경로를 전달한다.
- 반환된 `spreadsheetId`와 Google Sheets MIME 유형을 확인하고, 해당 스프레드시트를 다시 읽어 요청한 시트·범위·값·수식이 반영됐는지 확인한다. XLSX 저장이나 `keep_source_file_type` 업로드만으로 Google Sheets 제작을 완료했다고 하지 않는다.
- 이 경로는 Google Sheets 결과물을 요청받은 경우에만 사용한다. 로컬 XLSX·CSV 제작 요청을 업로드·공유·공개 승인으로 확대하지 않으며, 필요한 도구나 권한이 없으면 그 범위를 알린다.
