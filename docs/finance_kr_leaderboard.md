# 한국 데스크 모델 비교

이 문서는 `python -m agentworld_lite.finance leaderboard`가 저장된 실행 기록으로 만든 것입니다(모델 호출 없음). 각 실행 기록을 새 데스크에서 다시 실행해 답과 중간 계산을 함정 목록과 대조했습니다.

## 요약

| 모델 | 에피소드 | 성공 | 답 정답률 | 시드 | 라운드 사용(한도 대비) | 실패한 도구 호출/판 | 도구 호출 없는 턴 | 규칙 CCE | 비용 | 판당 비용 | 성공당 비용 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | 20 | 20 (100%) | 100% | 7 | 51% | 0.15 | 0% | 0.55 | $12.76 | $0.638 | $0.638 |
| claude-sonnet-5-5 | 40 | 40 (100%) | 100% | 7, 8 | 59% | 0.00 | 0% | 0.45 | $14.13 | $0.353 | $0.353 |
| claude-haiku-5-5 | 60 | 54 (90%) | 95% | 7, 8, 9 | 67% | 0.93 | 0% | 0.35 | $1.32 | $0.022 | $0.024 |
| ollama:qwen3:4b | 9 | 0 (0%) | 0% | 7 | 100% | 0.00 | 82% | 0.00 | $0.00 | $0.000 | - |

## 과제별 성공

| 모델 | k01 KB BPS·ROE | k02 신한·우리 2분기 CIR | k03 4대 2분기 순이익·보통주자본 | k04 2분기 순이익 증가율 | k05 합산 CIR | k06 밸류업 ROE 격차 |
|---|---|---|---|---|---|---|
| claude-opus-5-5 | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 실행 안 함 |
| claude-sonnet-5-5 | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 실행 안 함 |
| claude-haiku-5-5 | 9/12 | 10/12 | 12/12 | 12/12 | 11/12 | 실행 안 함 |
| ollama:qwen3:4b | 0/4 | 0/4 | 0/1 | 실행 안 함 | 실행 안 함 | 실행 안 함 |

## 방법론 위치와 채팅

공유형(shared)은 방법론 문장이 모두가 보는 문서에, 개인형(private)은 analyst의 개인 브리핑에만 있습니다. no_comm은 채팅을 막은 설정입니다.

| 모델 | 공유형·채팅 | 공유형·채팅 없음 | 개인형·채팅 | 개인형·채팅 없음 |
|---|---|---|---|---|
| claude-opus-5-5 | 5/5 | 5/5 | 5/5 | 5/5 |
| claude-sonnet-5-5 | 10/10 | 10/10 | 10/10 | 10/10 |
| claude-haiku-5-5 | 15/15 | 15/15 | 15/15 | 9/15 |
| ollama:qwen3:4b | 0/2 | 0/2 | 0/3 | 0/2 |

## 실패 분석: 틀리거나 빠진 답

| 모델 | 공유 누락 | 계산 안 함 | 입력 부족 | 합계 |
|---|---|---|---|---|
| claude-haiku-5-5 | 2 | 1 | 8 | 11 |
| ollama:qwen3:4b | 0 | 1 | 25 | 26 |

| 모델 | 에피소드 | 라운드 | 답 | 분류 | 근거 |
|---|---|---|---|---|---|
| claude-haiku-5-5 | k01_kb_bps_roe_private no_comm s7 | 18/18 | ttm_net_income_owners | 공유 누락 | calc.ttm_net_income_owners = 6,282,059,000,000 |
| claude-haiku-5-5 | k01_kb_bps_roe_private no_comm s7 | 18/18 | bps | 공유 누락 | calc.bps = 160,402 |
| claude-haiku-5-5 | k01_kb_bps_roe_private no_comm s7 | 18/18 | ttm_roe | 계산 안 함 | - |
| claude-haiku-5-5 | k01_kb_bps_roe_private no_comm s8 | 18/18 | ttm_roe | 입력 부족 | 필요한 수치 7개 중 2개 없음: 105560.2025H1.CFS.equity_owners, 105560.2025H1.CFS.hybrid_capital |
| claude-haiku-5-5 | k01_kb_bps_roe_private no_comm s9 | 18/18 | ttm_roe | 입력 부족 | 필요한 수치 7개 중 2개 없음: 105560.2025H1.CFS.equity_owners, 105560.2025H1.CFS.hybrid_capital |
| claude-haiku-5-5 | k02_q2_cost_income_private no_comm s7 | 20/20 | shinhan_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 2개 없음: 055550.2026H1.CFS.credit_loss_provision, 055550.2026Q1.CFS.credit_loss_provision |
| claude-haiku-5-5 | k02_q2_cost_income_private no_comm s7 | 20/20 | woori_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 2개 없음: 316140.2026H1.CFS.credit_loss_provision, 316140.2026Q1.CFS.credit_loss_provision |
| claude-haiku-5-5 | k02_q2_cost_income_private no_comm s8 | 20/20 | shinhan_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 2개 없음: 055550.2026H1.CFS.credit_loss_provision, 055550.2026Q1.CFS.credit_loss_provision |
| claude-haiku-5-5 | k02_q2_cost_income_private no_comm s8 | 20/20 | woori_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 2개 없음: 316140.2026H1.CFS.credit_loss_provision, 316140.2026Q1.CFS.credit_loss_provision |
| claude-haiku-5-5 | k05_big4_combined_cir_private no_comm s8 | 14/14 | sector_cost_income_ratio | 입력 부족 | 필요한 수치 12개 중 4개 없음: 105560.2026H1.CFS.credit_loss_provision, 055550.2026H1.CFS.credit_loss_provision 등 |
| claude-haiku-5-5 | k05_big4_combined_cir_private no_comm s8 | 14/14 | cost_income_spread_pp | 입력 부족 | 필요한 수치 6개 중 2개 없음: 316140.2026H1.CFS.credit_loss_provision, 105560.2026H1.CFS.credit_loss_provision |
| ollama:qwen3:4b | k01_kb_bps_roe_private full s7 | 18/18 | ttm_net_income_owners | 입력 부족 | 필요한 수치 3개 중 3개 없음: 105560.2025FY.CFS.net_income_owners, 105560.2025H1.CFS.net_income_owners 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_private full s7 | 18/18 | bps | 입력 부족 | 필요한 수치 4개 중 4개 없음: 105560.2026H1.CFS.equity_owners, 105560.2026H1.CFS.hybrid_capital 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_private full s7 | 18/18 | ttm_roe | 입력 부족 | 필요한 수치 7개 중 7개 없음: 105560.2025FY.CFS.net_income_owners, 105560.2025H1.CFS.net_income_owners 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_private no_comm s7 | 18/18 | ttm_net_income_owners | 입력 부족 | 필요한 수치 3개 중 3개 없음: 105560.2025FY.CFS.net_income_owners, 105560.2025H1.CFS.net_income_owners 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_private no_comm s7 | 18/18 | bps | 계산 안 함 | - |
| ollama:qwen3:4b | k01_kb_bps_roe_private no_comm s7 | 18/18 | ttm_roe | 입력 부족 | 필요한 수치 7개 중 5개 없음: 105560.2025FY.CFS.net_income_owners, 105560.2025H1.CFS.net_income_owners 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_shared full s7 | 18/18 | ttm_net_income_owners | 입력 부족 | 필요한 수치 3개 중 3개 없음: 105560.2025FY.CFS.net_income_owners, 105560.2025H1.CFS.net_income_owners 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_shared full s7 | 18/18 | bps | 입력 부족 | 필요한 수치 4개 중 4개 없음: 105560.2026H1.CFS.equity_owners, 105560.2026H1.CFS.hybrid_capital 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_shared full s7 | 18/18 | ttm_roe | 입력 부족 | 필요한 수치 7개 중 7개 없음: 105560.2025FY.CFS.net_income_owners, 105560.2025H1.CFS.net_income_owners 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_shared no_comm s7 | 18/18 | ttm_net_income_owners | 입력 부족 | 필요한 수치 3개 중 3개 없음: 105560.2025FY.CFS.net_income_owners, 105560.2025H1.CFS.net_income_owners 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_shared no_comm s7 | 18/18 | bps | 입력 부족 | 필요한 수치 4개 중 4개 없음: 105560.2026H1.CFS.equity_owners, 105560.2026H1.CFS.hybrid_capital 등 |
| ollama:qwen3:4b | k01_kb_bps_roe_shared no_comm s7 | 18/18 | ttm_roe | 입력 부족 | 필요한 수치 7개 중 7개 없음: 105560.2025FY.CFS.net_income_owners, 105560.2025H1.CFS.net_income_owners 등 |
| ollama:qwen3:4b | k02_q2_cost_income_private full s7 | 20/20 | shinhan_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 6개 없음: 055550.2026H1.CFS.general_admin_expense, 055550.2026Q1.CFS.general_admin_expense 등 |
| ollama:qwen3:4b | k02_q2_cost_income_private full s7 | 20/20 | woori_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 6개 없음: 316140.2026H1.CFS.general_admin_expense, 316140.2026Q1.CFS.general_admin_expense 등 |
| ollama:qwen3:4b | k02_q2_cost_income_private no_comm s7 | 20/20 | shinhan_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 6개 없음: 055550.2026H1.CFS.general_admin_expense, 055550.2026Q1.CFS.general_admin_expense 등 |
| ollama:qwen3:4b | k02_q2_cost_income_private no_comm s7 | 20/20 | woori_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 6개 없음: 316140.2026H1.CFS.general_admin_expense, 316140.2026Q1.CFS.general_admin_expense 등 |
| ollama:qwen3:4b | k02_q2_cost_income_shared full s7 | 20/20 | shinhan_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 6개 없음: 055550.2026H1.CFS.general_admin_expense, 055550.2026Q1.CFS.general_admin_expense 등 |
| ollama:qwen3:4b | k02_q2_cost_income_shared full s7 | 20/20 | woori_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 6개 없음: 316140.2026H1.CFS.general_admin_expense, 316140.2026Q1.CFS.general_admin_expense 등 |
| ollama:qwen3:4b | k02_q2_cost_income_shared no_comm s7 | 20/20 | shinhan_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 6개 없음: 055550.2026H1.CFS.general_admin_expense, 055550.2026Q1.CFS.general_admin_expense 등 |
| ollama:qwen3:4b | k02_q2_cost_income_shared no_comm s7 | 20/20 | woori_q2_cost_income_ratio | 입력 부족 | 필요한 수치 6개 중 6개 없음: 316140.2026H1.CFS.general_admin_expense, 316140.2026Q1.CFS.general_admin_expense 등 |
| ollama:qwen3:4b | k03_big4_q2_sector_private full s7 | 30/30 | kb_q2_net_income_owners | 입력 부족 | 필요한 수치 2개 중 2개 없음: 105560.2026H1.CFS.net_income_owners, 105560.2026Q1.CFS.net_income_owners |
| ollama:qwen3:4b | k03_big4_q2_sector_private full s7 | 30/30 | shinhan_q2_net_income_owners | 입력 부족 | 필요한 수치 2개 중 2개 없음: 055550.2026H1.CFS.net_income_owners, 055550.2026Q1.CFS.net_income_owners |
| ollama:qwen3:4b | k03_big4_q2_sector_private full s7 | 30/30 | hana_q2_net_income_owners | 입력 부족 | 필요한 수치 2개 중 2개 없음: 086790.2026H1.CFS.net_income_owners, 086790.2026Q1.CFS.net_income_owners |
| ollama:qwen3:4b | k03_big4_q2_sector_private full s7 | 30/30 | woori_q2_net_income_owners | 입력 부족 | 필요한 수치 2개 중 2개 없음: 316140.2026H1.CFS.net_income_owners, 316140.2026Q1.CFS.net_income_owners |
| ollama:qwen3:4b | k03_big4_q2_sector_private full s7 | 30/30 | common_equity_average | 입력 부족 | 필요한 수치 8개 중 8개 없음: 105560.2026H1.CFS.equity_owners, 105560.2026H1.CFS.hybrid_capital 등 |
| ollama:qwen3:4b | k03_big4_q2_sector_private full s7 | 30/30 | common_equity_spread | 입력 부족 | 필요한 수치 4개 중 4개 없음: 105560.2026H1.CFS.equity_owners, 105560.2026H1.CFS.hybrid_capital 등 |

## 중간 계산에서 나온 함정 값

제출 여부와 관계없이, 계산 결과가 함정 목록의 값과 같았던 경우입니다(최종 답이 맞았는지 함께 적음).

중간 계산에서도 함정 값이 나오지 않았습니다.

## 참고

- k06은(는) 어느 모델로도 실행하지 않았습니다.
- ollama:qwen3:4b: 실행 설정상 20판 중 9판만 기록되어 있습니다(진행 중이거나 중단된 실행).
