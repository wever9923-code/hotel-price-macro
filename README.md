# 파타야 호텔 가격 매크로

GitHub Actions(무료)에서 하루 2번(08:50, 14:50) 자동으로 돌아가요. 컴퓨터가 꺼져 있어도 됩니다.

- 몽키트래블 · 아고다 · 부킹닷컴 · 태초클럽(참고용)에서 3개 호텔 가격 확인
- `docs/history.json`에 기록 → GitHub Pages 가격 페이지에 반영
- 직전 기록과 가격이 달라지면 카카오톡 "나와의 채팅"으로 알림

조건(날짜·인원·호텔)은 `scraper/check.py` 맨 위 "여행 조건" 부분에서 바꿀 수 있어요.

---

## 설정 순서 (처음 한 번, 약 20분 · PC에서 하는 걸 추천)

### 1. GitHub 저장소 만들기
1. https://github.com 가입 → 오른쪽 위 **+ → New repository**
2. 이름 `hotel-price-macro`, **Public** 선택 → Create
   (무료 GitHub Pages는 Public 저장소에서만 돼요. 여행 날짜와 가격 기록이 공개된다는 점만 참고하세요.)
3. **uploading an existing file** 클릭 → 압축을 푼 폴더 안의 파일과 폴더를 **전부** 끌어다 놓기
   (`.github` 폴더가 꼭 포함돼야 해요. 숨김 폴더라 안 보이면 탐색기에서 "숨긴 항목 표시"를 켜세요)
4. **Commit changes**

### 2. 저장소 설정
- **Settings → Pages**: Source = *Deploy from a branch*, Branch = `main` / `/docs` → Save
  → 1~2분 뒤 `https://내아이디.github.io/hotel-price-macro/` 에서 가격 페이지가 열려요.
- **Settings → Actions → General → Workflow permissions**: *Read and write permissions* 선택 → Save

### 3. 카카오 앱 만들기 (카톡 알림용)
1. https://developers.kakao.com 카카오 계정으로 로그인 → **내 애플리케이션 → 애플리케이션 추가하기**
2. 만든 앱 → **앱 키**에서 **REST API 키** 복사해 두기
3. **카카오 로그인** → 활성화 **ON**, **Redirect URI**에 `https://localhost` 등록
4. **카카오 로그인 → 동의항목** → "카카오톡 메시지 전송"을 **선택 동의**로 설정
5. **플랫폼 → Web** → 사이트 도메인에 `https://내아이디.github.io` 등록 (알림의 "가격 페이지" 버튼용)
6. (보안 → Client Secret을 켰다면 그 코드도 복사)

### 4. GitHub 토큰 만들기 (카카오 토큰 자동 갱신용)
1. GitHub 오른쪽 위 프로필 → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
2. Repository access: *Only select repositories* → `hotel-price-macro`
3. Permissions → Repository permissions → **Secrets: Read and write**
4. 만료는 최대(1년)로 → Generate → 토큰 복사

### 5. Secret 등록
저장소 **Settings → Secrets and variables → Actions → New repository secret**
| 이름 | 값 |
|---|---|
| `KAKAO_REST_KEY` | 3-2의 REST API 키 |
| `GH_PAT` | 4의 GitHub 토큰 |
| `KAKAO_CLIENT_SECRET` | (Client Secret을 켠 경우만) |

### 6. 카카오 연결
1. 아래 주소에서 `REST키`를 바꿔 브라우저로 열기 → 동의
   `https://kauth.kakao.com/oauth/authorize?client_id=REST키&redirect_uri=https://localhost&response_type=code&scope=talk_message`
2. "사이트에 연결할 수 없음"이 떠도 정상이에요. 주소창의 `code=` 뒤 값을 복사 (10분 안에 사용)
3. 저장소 **Actions → 카카오 연결 (처음 한 번) → Run workflow** → code에 붙여 넣고 실행
4. 카톡 "나와의 채팅"에 "카카오 연결 완료!" 메시지가 오면 성공

### 7. 첫 실행
**Actions → 호텔 가격 확인 → Run workflow**. 끝나면 로그의 "가격 확인" 단계에서 사이트별 결과를 볼 수 있어요.
```
[monkey] 3/3 완료 ...
[agoda] 3/3 완료 ...
[booking] 3/3 완료 ...
```
`0/3`인 사이트는 GitHub 서버 접속이 막힌 거예요. 그 사이트는 기록되지 않고 나머지는 정상 작동해요.

---

## 참고
- 카카오 refresh token은 2개월마다 만료되지만, 매크로가 만료 전에 자동으로 새로 받아 Secret을 갱신해요(GH_PAT 필요). 알림이 끊기면 6번만 다시 하면 돼요.
- GitHub 예약 실행은 서버 사정으로 몇 분~수십 분 늦어질 수 있어요.
- 60일 동안 저장소에 변화가 없으면 GitHub가 예약 실행을 멈추는데, 매크로가 매번 기록을 커밋해서 보통은 해당되지 않아요.
