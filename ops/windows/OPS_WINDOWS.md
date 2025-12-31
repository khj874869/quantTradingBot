# Windows 24/7 운영 가이드 (QuantBot)

## 0) 설치
- Python 3.10+ 권장(3.11 추천)
- 프로젝트 폴더에서:
  - `pip install -r requirements.txt` 형태가 아니라면 `pip install -e .` 또는 `pip install .` 사용

## 1) .env 설정
1. 프로젝트 루트에 `.env.example`를 복사해서 `.env`로 만듭니다.
2. 키를 채워 넣습니다.
3. 최초에는 반드시:
   - `TRADING_ENABLED=false`
   - `--mode paper` 또는 `--mode live` + `TRADING_ENABLED=false`(드라이런)
로 충분히 검증하세요.

## 2) 절전/슬립 끄기(중요)
- 제어판 → 전원 옵션
  - 절전: 안 함
  - 최대절전: 끄기
  - 디스크 절전: 끄기
  - USB 절전: 끄기

## 3) 실행(수동)
- 멀티봇:
  - `powershell -ExecutionPolicy Bypass -File ops\windows\run_multi_runner.ps1 -ProjectRoot "C:\path\to\quantbot_complete_final"`
- 대시보드:
  - `powershell -ExecutionPolicy Bypass -File ops\windows\run_dashboard.ps1 -ProjectRoot "C:\path\to\quantbot_complete_final"`

## 4) 자동 실행(권장)
### 옵션 A: 작업 스케줄러(간단)
관리자 PowerShell:
- `ops\windows\install_tasks.ps1 -ProjectRoot "C:\path\to\quantbot_complete_final"`

### 옵션 B: NSSM 서비스(가장 추천)
1) nssm.exe 다운로드 후 `ops\windows\nssm.exe`로 배치
2) 관리자 PowerShell:
- `ops\windows\install_nssm_service.ps1 -ProjectRoot "C:\path\to\quantbot_complete_final" -PythonExe "C:\Python311\python.exe"`

## 5) 실매매 전환 체크리스트
- [ ] 출금 권한 OFF
- [ ] IP 화이트리스트 가능하면 ON
- [ ] `TRADING_ENABLED=true` 전환 전 최소 며칠간 페이퍼/드라이런 로그 확인
- [ ] 일 손실 제한(MAX_DAILY_LOSS) 및 글로벌 익스포저 제한 설정
- [ ] 대시보드에서 fills/손익/포지션이 정상인지 확인
