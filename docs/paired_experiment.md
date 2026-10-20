# 정보 배치 × 채팅: 동일 과제 비교 실험

기존 `gen`의 low/mid/high/max는 서로 다른 과제를 뽑고, 공유 문서의 계획 삭제, 목표 문구, 월드 가이드, 라운드 제한도 함께 바꿉니다. 따라서 `docs/results_generated.md`의 단계별 차이는 정보 배치만의 효과가 아닙니다. 이 실험은 그 요인을 분리합니다.

## 설계

`gen-pairs`는 정답 시나리오를 실행해 통과한 청사진마다 두 과제를 저장합니다.

- `*_shared`: 레시피, 팀, 인계 계획, 위치 정보가 공유 문서에 있습니다.
- `*_private`: **동일한 정보 문장**이 각자의 개인 브리핑으로 한 번씩 옮겨집니다. 레시피는 제작자, 역할과 위치는 해당 에이전트, 인계 계획은 받는 사람에게 갑니다. 받는 사람이 먼저 알려 주지 않으면 주는 사람은 인계 상대를 문서에서 알 수 없습니다.

두 버전의 에이전트 이름·스킬·초기 아이템·스폰, 월드, 목표 문구, 검증기, 정답 시나리오, 월드 가이드, 라운드 제한은 같습니다. 과제 ID에만 배치 이름을 붙여 실행 결과를 별도로 저장합니다. 모델 프롬프트에는 과제 ID가 나타나지 않습니다.

각 버전에서 `full`(채팅 허용)과 `no_comm`(채팅 도구 제거)을 실행합니다. 추가 `relay` 조건은 채팅 도구를 제거한 채, 받는 사람의 인계 계획 문장을 0라운드에 주는 사람의 채팅 로그로 전달합니다. 이 조건은 필요한 정보 전달이 실패를 구제하는지 살피는 진단용 상한입니다. 메시지 작성에 에이전트 턴을 쓰지 않으므로 정상 채팅과 동일 비용의 비교가 아닙니다.

## 실행

```bash
python -m agentworld_lite gen-pairs --n 20 --seed 7 --out tasks/paired
python -m agentworld_lite validate --tasks tasks/paired
python -m agentworld_lite run --tasks tasks/paired --agent llm --provider ollama --model gemma4:cloud --settings full no_comm relay --seeds 7 8 9 --shuffle-jobs --job-seed 7 --out runs/paired_gemma
python -m agentworld_lite pair-report --runs runs/paired_gemma --out reports/paired_gemma.md
```

`tasks/paired`에는 시드 7로 생성한 8쌍의 예시가 들어 있습니다. 위 생성 명령은 20쌍으로 다시 구성합니다. `relay`는 공유형에서는 주입할 메시지가 없어 `no_comm`과 같으므로, 보고서는 분산형 `relay`만 집계합니다. 동일한 `--out`의 기존 궤적은 `run`이 건너뜁니다. 생성 세트를 바꾸거나 모델을 바꾸면 새로운 출력 디렉터리를 사용하세요.

`--shuffle-jobs --job-seed 7`은 조건 실행 순서를 재현 가능하게 섞어 시간에 따른 모델·서버 상태의 영향을 특정 조건에 몰리지 않게 합니다. 셔플 설정과 난수 시드는 `run_config.json`에 기록됩니다.

모델 요청에도 `--seeds` 값을 전달하지만, `gemma4:cloud` 스모크 실행과 후속 실행에서 같은 시드 7의 행동 궤적이 달랐습니다. 시드는 요청 설정이지 클라우드 응답의 완전한 재현 보장이 아닙니다. 반복 실행은 이 변동을 살피기 위한 것이고, 원본 궤적을 함께 보존해야 합니다.

## 해석

주 지표는 같은 청사진·시드에서 계산한 성공 여부의 차이입니다. 공유형 채팅 효과와 분산형 채팅 효과를 구한 뒤 그 차이를 비교합니다. 시드 3개는 독립적인 새 과제가 아니라 같은 청사진의 반복입니다. 보고서는 완전한 네 조건 블록만 집계하고 청사진별 결과를 별도로 표시합니다.

`relay`가 성공을 회복해도 모델의 자발적 메시지가 정확했다는 뜻은 아닙니다. 반대로 회복하지 못하면 정보 이외의 계획·도구 사용 문제가 남았을 수 있습니다. CCE는 성공 궤적 분석에 보조적으로 사용하고, 주 결론은 성공률의 동일 과제 비교에 둡니다.

## 동작 확인용 소규모 실행

2026-10-08, `gemma4:cloud`, 시드 7로 쉬운 조리 과제 `p07003`과 인계 5회가 필요한 `p07007`을 골라 실행했습니다. 과제를 의도적으로 고른 2쌍의 스모크 테스트이며, 효과 크기를 추정하기 위한 표본이 아닙니다.

| 과제 | 공유형 채팅 허용/금지 | 개인형 채팅 허용/금지 | 개인형 relay |
|---|---|---|---|
| `p07003` | 성공/성공 | 성공/성공, 채팅 0회 | 성공 |
| `p07007` | 성공/성공 | 성공/실패, 채팅 허용 시 6회 | 성공 |

`p07007` 공유형은 두 조건 모두 11라운드에 끝났습니다. 개인형은 채팅 허용 29라운드, 금지 36라운드 실패, `relay` 28라운드였습니다. 이 한 사례는 정보 전달이 병목일 수 있음을 보여 주지만, 같은 현상이 여러 청사진과 시드에서 반복되는지는 위 전체 프로토콜로 확인해야 합니다.

## 8쌍 본 실행 상태 (2026-10-08)

예시 8쌍 × 시드 7 × 핵심 네 조건(`full`, `no_comm`; 공유형·개인형)으로 32개 에피소드를 시작했습니다. `gemma4:cloud` 무료 사용 한도 HTTP 429로 20개가 완료된 뒤 중단됐습니다. 셔플된 순서에서 아직 네 조건이 모두 완료된 청사진은 없어 성공률 비교를 집계하지 않습니다. 저장된 궤적은 `runs/paired_gemma_main/trajectories`에 있습니다(실행 디렉터리는 `.gitignore` 대상).

한도가 해제되면 아래 명령으로 이어서 실행할 수 있습니다. 기존 20개 파일은 건너뜁니다. 완료 후에만 `pair-report`를 실행합니다.

```bash
python -m agentworld_lite run --tasks tasks/paired --agent llm --provider ollama --model gemma4:cloud --settings full no_comm --seeds 7 --shuffle-jobs --job-seed 7 --out runs/paired_gemma_main
python -m agentworld_lite pair-report --runs runs/paired_gemma_main --out reports/paired_gemma_main.md
```

한도 발생 전후 실행 시점이 달라지므로, 클라우드 모델 상태 변화가 남는 한계도 기록해야 합니다. Ollama 클라이언트는 일시적인 429를 재시도하지만 명시적인 `Free usage limit` 응답은 즉시 반환해 긴 대기를 피합니다.
