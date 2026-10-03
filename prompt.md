현재 이 프로젝트(01_kidsnote_bot)의 GitHub 리포지토리를 무료로 GitHub Pages를 사용하기 위해 Private에서 Public(공개)으로 전환하려고 합니다.

코드가 퍼블릭으로 공개되기 전에 보안상 문제가 될 수 있는 민감한 정보가 프로젝트 내에 포함되어 있는지 전체 코드와 파일 구조를 점검해 주세요.

특히 다음 항목들을 중점적으로 확인해 주세요:

Google OAuth 및 API 관련 인증 정보 (Client ID, Client Secret, API 키 등)

키즈노트 로그인 계정 정보 (아이디, 비밀번호)

Gemini API 키 등 기타 외부 서비스 API 키

.env, credentials.json 등 인증 정보가 담긴 파일이 .gitignore에 누락되어 Git에 추적되고 있는지 여부

점검 후 다음 내용을 알려주세요:

외부로 노출되면 안 되는 파일 및 하드코딩된 변수 목록

하드코딩된 민감 정보를 환경 변수(.env)로 안전하게 분리하는 방법

이미 Git 커밋 기록에 남아있는 민감한 파일/코드를 히스토리에서 완전히 삭제하는 명령어 절차 (bfg repo-cleaner 또는 git filter-branch 등)

최종적으로 안전하게 Public 리포지토리로 전환하는 확인 체크리스트
