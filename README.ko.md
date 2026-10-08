# AgentWorld-Lite

[English](README.md) · 한국어

> **비공식 구현입니다.** AgentWorld·AutoGym 논문 저자들과 관계가 없으며, 원 논문의 환경·수치와 직접 비교되지 않습니다.

[AgentWorld (arXiv:2609.31590)](https://arxiv.org/abs/2609.31590) 논문의 평가 파이프라인을 경량 Python으로 재구현한 프로젝트입니다. RPG 샌드박스에서 여러 LLM 에이전트가 **비대칭 역할**을 나눠 맡고 **채팅으로만 협업**해 장기 과제를 풉니다. 결과는 성공률(SR/PSR)과 **인과 협업 효율(CCE/PAC)**, 그리고 **소통 실패 유형**으로 평가합니다.

- 논문 분석: [docs/paper_analysis.md](docs/paper_analysis.md)
- AutoGym 통합 설계: [docs/autogym_integration.md](docs/autogym_integration.md)
- 원 논문은 Kaetram(Node.js MMORPG)을 쓰지만, 이 프로젝트는 같은 프로토콜을 지키는 자체 격자 시뮬레이터를 씁니다. 그래서 설치는 `pip` 하나로 끝납니다.

## 파이프라인

```
 tasks/*.yaml ──validate──▶ (정적 검사 + 정답 시나리오 재생)
      │
      ├─augment──▶ Claude가 변형 생성 → 시뮬레이터에서 정답 재생 통과분만 채택
      │
      ▼
   run ──▶ 턴제 라운드로빈 × 블랙박스 에이전트(Claude / Ollama 로컬·클라우드 모델 / 스크립트 / 랜덤)
      │      설정: full · no_comm · no_docs · whitebox · random_spawn
      ▼
 runs/<name>/trajectories/*.json   (모든 행동·효과·체크포인트·라운드별 스냅샷)
      │
   evaluate ──▶ CCE/PAC (rule | llm | both) + 판정자 일치도(κ) + 실패 유형 6종
      ▼
   report ──▶ reports/*.md (모델×설정 표, 카테고리별 SR, 실패 분포, 인과 그래프 Mermaid)
```

## 빠른 시작

```bash
pip install -e ".[dev]"
```

핵심 의존성은 `pyyaml` 하나입니다. Claude를 쓸 때만 `pip install -e ".[claude]"`로 Anthropic SDK를 추가합니다.

API 키 없이 전체 흐름을 확인하려면 아래 순서로 실행합니다. 정답 스크립트는 오라클, 랜덤 에이전트는 하한 역할입니다.

```bash
python -m agentworld_lite validate
```

```bash
python -m agentworld_lite run --agent scripted --out runs/oracle
```

```bash
python -m agentworld_lite run --agent random --settings full no_comm --seeds 1 2 3 --out runs/random
```

```bash
python -m agentworld_lite evaluate --run runs/oracle --judge rule
```

```bash
python -m agentworld_lite report --runs runs/oracle runs/random --out reports/baseline.md --graphs 3
```

### 무료 모델로 실행 (Ollama)

[Ollama](https://ollama.com)를 설치하면 로컬 오픈 모델이나 Ollama 클라우드 모델로 무료로 돌릴 수 있습니다. 비용 계산은 0으로 기록됩니다.

- **로컬 모델**: `ollama pull <모델>`로 받은 뒤 `--model <모델>`로 지정합니다. 도구 호출을 지원하는 모델이어야 합니다. 프롬프트가 약 4.5천 토큰이라 컨텍스트 창은 기본 8192로 엽니다(`--num-ctx`로 변경).
- **클라우드 모델**: `ollama signin`으로 로그인한 뒤 `gemma4:cloud`처럼 `:cloud`로 끝나는 이름을 씁니다. 로컬 Ollama 서버를 거쳐 호출되므로 코드 변경이 없습니다. 클라우드는 JSON 형식 강제를 지원하지 않아, CCE 판정과 실패 분류는 프롬프트에 스키마를 넣고 답에서 JSON을 뽑아내는 방식으로 자동 전환됩니다.

```bash
python -m agentworld_lite run --agent llm --provider ollama --model gemma4:cloud --only t01_magic_staff --out runs/gemma_smoke --verbose
```

판정도 무료 모델로 돌릴 수 있습니다.

```bash
python -m agentworld_lite evaluate --run runs/gemma_smoke --judge both --judge-provider ollama --judge-model gemma4:cloud
```

Ollama는 기본적으로 요청을 하나씩 처리하므로 `--provider ollama`일 때 병렬 실행 기본값은 1입니다. 작은 모델은 도구 호출을 JSON 텍스트로 쓰는 경우가 있는데, 이것도 해석해서 실행하고 `note: text_tool_call`로 표시합니다.

### Claude 에이전트로 실행

`ANTHROPIC_API_KEY`를 설정하거나 `ant auth login`으로 로그인한 뒤 실행합니다. 기본 모델은 `claude-opus-5-5`입니다. 논문과 같은 모델로 비교하려면 `--model claude-haiku-4-5`를 씁니다.

먼저 과제 하나로 동작을 확인합니다.

```bash
python -m agentworld_lite run --agent llm --only t01_magic_staff --out runs/smoke --verbose
```

이어서 전체 과제와 소거 설정을 실행합니다.

```bash
python -m agentworld_lite run --agent llm --model claude-opus-5-5 --settings full no_comm whitebox --out runs/opus55
```

마지막으로 LLM 판정 CCE와 실패 유형 분석까지 돌려 리포트를 만듭니다.

```bash
python -m agentworld_lite evaluate --run runs/opus55 --judge both --failures
```

```bash
python -m agentworld_lite report --runs runs/opus55 runs/random --out reports/opus55.md --graphs 3
```

이미 저장된 궤적은 건너뛰므로, 중단된 실행은 같은 명령으로 이어서 돌릴 수 있습니다.

## 과제 (tasks/main)

| ID | 카테고리 | 에이전트 | 라운드 | 협업 포인트 |
|---|---|---|---|---|
| t01_magic_staff | 제작 | 3 | 25 | 벌목 → 막대 가공 → 지팡이 제작·장착 (도구·스킬 분산) |
| t02_supply_run | 채집 | 3 | 30 | 숲·광산 두 곳에서 모아 채집 불가 역할에게 납품 |
| t03_ogre_hunt | 전투 | 4 | 25 | 재생하는 보스에게 같은 라운드에 집중 공격하고, 힐러가 음식 보급 |
| t04_market_day | 거래 | 3 | 30 | 상점 거래 권한은 상인만 → 코인·광석을 모아 철검 구매 |
| t05_lost_shrine | 탐험 | 3 | 25 | 추적자만 성소를 볼 수 있음 → 좌표 공유 후 전원 집결 |
| t06_wolf_siege | 생존 | 3 | 14 | 늑대 포위 속 10라운드 생존 (모닥불 건설, 요리, 탱킹) |
| t07_outpost_walls | 건설 | 4 | 40 | 통나무·광석 → 판자·못 → 방책 건설 (4단계 공급망) |
| t08_crystal_plates | 협응 | 4 | 20 | 각자 다른 발판 위치를 알고 있음 → 정보 교환 후 4명이 동시에 점유 |
| t09_harvest_feast | 협응 | 6 | 30 | 어부 2·채집꾼 2 → 요리사 → 주최자 (6인 공급망) |

모든 과제는 테스트로 다음을 보장합니다. 정적 검증 통과, 시작 시점엔 미달성, 정답 시나리오로는 예산 안에 성공, 아무것도 안 하는 팀은 실패, 정적 분석상 한 명이 혼자 풀 수 없음.

### 과제 자동 생성 (AutoGym식 청사진 우선)

해답 경로(누가 무엇을 모으고 만들어 누구에게 넘기는지)를 먼저 짜고, 거기서 과제·검증기·정답 시나리오를 만듭니다. 정답 시나리오를 시뮬레이터에서 실제로 돌려 통과한 과제만 저장하며, 실패하면 자원 노드 추가·스폰 재배치·권한 강화로 최대 3회 수리합니다. LLM 호출이 없습니다.

```bash
python -m agentworld_lite gen --n 30 --seed 7 --out tasks/gym
```

생성 파라미터(레시피 깊이, 팀 구조, 정보 은닉 정도, 수량, 팀 규모)의 샘플링 가중치는 `--weights` JSON으로 바꿀 수 있습니다. 생성된 과제는 다른 과제와 똑같이 `run --tasks tasks/gym`으로 실행합니다.

### 새 과제 추가하기

`tasks/main/`의 YAML을 복사해 수정합니다. 주요 필드:

- `agents[]`: `username`, `role`, `spawn`, `skills`, `inventory`, `equipment`, `restricted_tools`(역할 제한), `view_radius`, `role_brief`(본인만 아는 정보)
- `extra_entities[]`: 보스(`hp`/`regen`/`attack` 덮어쓰기), 숨은 랜드마크(`reveal: {foraging: 20}`), 발판(`color`) 등
- `verifier.checkpoints[]`: `has_item`, `team_total`, `equipped`, `kills`, `all_alive`, `in_region`, `near`, `structure`, `plates`, `entity_defeated`, `rounds_survived`
- `reference_solution`: 에이전트별 `[tool, {args}, {retries: n}]` 목록. `validate`가 이것을 실제로 재생해 풀 수 있는 과제인지 확인합니다.

## 산출물과 지표

| 지표 | 정의 |
|---|---|
| SR | 검증기 기준 성공 비율 |
| PSR | 체크포인트 달성률 평균 (`3/5` 같은 수량은 부분 점수) |
| CCE | \|기여 행동\| / \|전체 행동\|. 실패 에피소드는 0 (성공 행동이 없어 기여 집합이 비므로) |
| CCE \| success | 성공 에피소드만의 CCE 평균 (효율 자체를 비교할 때 사용) |
| PAC_i | 에이전트 i의 행동 중 기여 행동 비율. 최소 PAC로 무임승차 에이전트를 찾을 수 있음 |
| 판정자 일치도 | 규칙 판정 vs LLM 판정의 원시 일치율·Cohen's κ (논문의 사람-LLM 검증에 대응) |
| 실패 유형 | 낡은·중복 / 오인 / 사실 오류 / 성급한 완료 / 비실질 / 논리 불일치 |

궤적 JSON에는 행동마다 도구 호출, 결과, 인벤토리·위치 변화(`effects`), 체크포인트 진척(`cp_have`), 모델의 사고 텍스트(`reasoning`, 다른 에이전트에게는 공유되지 않음)가 남습니다. `python -m agentworld_lite show <trajectory.json> --reasoning`으로 읽을 수 있습니다.

## 비용 감각 (대략치)

호출 1회의 입력은 약 3천 토큰입니다(고정부 약 2천 + 관측 약 0.9천). 과제 9개를 라운드 예산까지 모두 쓰면 최대 892회 호출입니다. 성공하면 일찍 끝나므로 실제 호출은 이보다 적습니다.

| 에이전트 모델 | 1회 대략 | 전체 9과제 1회 실행 상한 |
|---|---|---|
| claude-haiku-4-5 | ~$0.004 | ~$4 |
| claude-sonnet-5-5 | ~$0.01 | ~$10 |
| claude-opus-5-5 | ~$0.02–0.03 | ~$20–27 |

출력 토큰(사고량)에 따라 크게 달라집니다. 실제 비용은 실행 로그와 리포트의 `Cost $` 열(응답의 usage 기준 추정)로 확인하세요. LLM 판정 CCE는 성공 에피소드당 라운드 수만큼 추가 호출이 발생합니다. 고정 프롬프트가 2천 토큰 안팎이라, 모델별 최소 캐시 길이에 못 미치면 프롬프트 캐싱이 적용되지 않을 수 있습니다.

## API 사용 방식

- 에이전트 턴마다 [과제 컨텍스트(캐시 지점) | 현재 관측]으로 요청을 새로 구성합니다. 논문의 "매 라운드 새 관측" 프로토콜을 따른 것입니다.
- `tool_choice: auto` + `disable_parallel_tool_use`로 턴당 도구 1개를 강제합니다. 최신 모델은 강제 도구 호출(`any`)을 지원하지 않기 때문입니다. 도구를 호출하지 않으면 한 번 재촉하고, 그래도 안 하면 `wait`로 기록합니다(`note: no_tool_call`).
- 도구 스키마는 `strict: true`로 고정합니다. 끄려면 `--no-strict`를 씁니다.
- Opus 5.5 / Sonnet 5.5 / Fable 5.1에는 서버 측 거절 폴백(`fallbacks: "default"`)을 기본으로 켭니다. 끄려면 `--no-fallbacks`를 씁니다.
- `--effort`는 effort를 지원하는 모델에만 전달합니다(Haiku 4.5 제외).

## 구조

```
agentworld_lite/
  content.py      아이템·레시피·자원·몬스터·상점·건축물
  gamemap.py      64x48 맵, 지역, BFS 경로, 기본 개체 배치(고정 시드)
  engine.py       월드 상태 + 13개 고수준 도구 + 라운드 종료 처리(어그로, 재생, 리스폰)
  tools.py        Claude 도구 스키마
  observation.py  에이전트별 구조화 텍스트 관측
  task.py         과제 로딩·검증·프롬프트 컨텍스트·협업 필요성 정적 분석
  verifier.py     선언형 체크포인트 → 성공/PSR
  agents.py       LLMAgent(Claude), ScriptedAgent(정답 재생), RandomAgent
  llm.py          Anthropic SDK 래퍼(모델별 파라미터, JSON 호출, 사용량·비용 집계)
  local_llm.py    Ollama 백엔드(로컬·클라우드 모델, 표준 라이브러리만 사용)
  runner.py       에피소드 실행·소거 설정·병렬 스위트
  cce.py          CCE/PAC: RuleJudge, LLMJudge(논문 프롬프트), 일치도, Mermaid 그래프
  failures.py     소통 실패 유형 분류
  augment.py      자기 검증형 과제 증강
  report.py       Markdown 리포트
  blueprint.py    청사진 생성: 파라미터 → 해답 경로·역할·인계 → 과제 YAML
  gym.py          정답 실행·수리·예산·수율 (과제 자동 생성 루프)
tasks/main/       과제 9개 (8개 카테고리)
tasks/gym/        자동 생성 과제 (gen 산출물)
tests/            엔진·과제·CCE·생성기·Claude/Ollama 요청 형식 테스트 (API 호출 없음)
```

테스트 실행:

```bash
python -m pytest -q
```

## 논문과의 차이

- 환경 규모: 1056×768·아이템 380+ 대신 64×48·약 40종. 레벨업 없음.
- 도구 13개는 논문이 전체 목록을 공개하지 않아 재구성했습니다.
- 과제는 100+100개가 아니라 9개(카테고리별 1~2개)입니다. 대신 `augment`로 검증된 변형을 늘릴 수 있습니다.
- 오라클 소통은 `whitebox`(동료 상태 전체 공개)로 근사했고, 단일 에이전트·공유 계획 소거는 구현하지 않았습니다.
- CCE 판정 모델은 GPT-4.1 대신 Claude입니다. 규칙 기반 판정자를 추가해 일치도를 함께 봅니다.
- 원 환경과 수치가 직접 비교되지는 않습니다. 모델 간 **상대 비교**와 실패 분석 도구로 쓰세요.

## 라이선스와 인용

MIT 라이선스입니다. CCE 판정 프롬프트는 AgentWorld 논문의 절차와 문구를 바탕으로 했습니다. 이 저장소를 쓰신다면 두 원 논문을 인용해 주세요(BibTeX는 [영어 README](README.md#citing) 참고).
