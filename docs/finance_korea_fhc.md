# 한국 데스크: 4대 금융지주 실제 DART 공시

**English summary.** The finance research desk (a collector fetches filings, an analyst computes, a reporter submits,
and every number is a cited fact) now runs on real DART filings of Korea's four largest financial holding companies:
KB금융 (105560), 신한지주 (055550), 하나금융지주 (086790) and 우리금융지주 (316140). A new `fhc` account profile reads
their bank-format statements (net interest and fee income, G&A, credit-loss provisions, net income to owners, equity
with hybrid capital shown separately) by XBRL `account_id`, with fallbacks where the groups or periods differ. The
dataset (`data/finance/dart_fhc`) covers 2025 Q1, H1, Q3 and FY and 2026 Q1 and H1, records the original filing of
every amended report, and matches each group's 2Q 2026 earnings release. The desk is DART-only: DART has no
market prices, the KRX Open API's terms forbid passing its data to third parties, and every answer should be
verifiable from filings alone, so no prices ship with the repository. Tasks in `tasks/finance_kr` (KB's BPS and TTM
ROE, the Q2 cost-to-income ratios of Shinhan and Woori, and a four-group comparison of Q2 net income to owners and
common equity) come as shared/private pairs with a 0.1% tolerance; every listed mistake misses it. The sector note is
written in Korean by fixed rules and cites a DART receipt number (접수번호) after every figure. A Korean valuation
snapshot (P/B against ROE, marked as an educational sample with no rating and no target price) exists but stays off
until a user adds sourced closing prices. A Korean Streamlit app (`demo/streamlit_app_kr.py`) shows the replay, the
data, the note and the tasks offline.

이 문서는 숫자를 어디서 가져왔는지, 어떻게 정규화했는지, 무엇과 대조했는지, 그 숫자로 만든 과제와 산출물(섹터 노트,
밸류에이션 스냅샷, 데모)을 정리합니다. 설계의 기준은 캐나다 데스크([finance_canada.md](finance_canada.md))이고, 가상
회사로 만든 기존 한국 과제(`tasks/finance`)와 캐나다 과제는 바꾸지 않았습니다.

이 데스크는 **DART 공시만** 씁니다. DART에는 주가가 없고, KRX Open API 약관은 데이터를 제3자에게 넘기는 것을 금지하며,
모든 답을 공시만으로 검증할 수 있게 하려는 것이 이 데스크의 설계 목표이기 때문입니다. 그래서 저장소에는 주가가 하나도
없고, 주가가 필요한 PER·PBR 대신 BPS와 ROE를 과제로 냈습니다.

## 수집 경로

| 경로 | 무엇인가 | 자동 수집 | 이 프로젝트에서 |
|---|---|---|---|
| DART OpenAPI | 금융감독원 전자공시의 공식 API. 무료 인증키 필요 | 허용(하루 2만 건) | `dart` 소스, `--profile fhc` |
| KRX 정보데이터시스템 / KRX Open API | 시세 | Open API는 키 보유자에게 허용되지만 약관 제11조 2항이 데이터를 제3자에게 제공하는 것을 금지 | 쓰지 않음(아래 "주가를 쓰지 않는 이유") |
| 각 그룹의 실적 발표(IR) | 잠정 실적 보도자료·발표 자료 | 수집하지 않음 | 숫자 대조에만 사용 |

### 인증키

opendart.fss.or.kr에서 무료 키를 직접 발급받아 환경변수에 넣습니다. 코드는 키를 출력하거나 파일에 저장하지 않고, 오류
메시지에도 요청 URL(키 포함)을 넣지 않습니다. 키가 없으면 `fetch`는 키를 설정하라는 메시지를 내고 멈춥니다.

```powershell
$env:DART_API_KEY = "발급받은 키"
```

```bash
python -m agentworld_lite.finance fetch --source dart --profile fhc --company 105560 055550 086790 316140 --periods 2025:Q1 2025:H1 2025:Q3 2025:FY 2026:Q1 2026:H1 --cache C:/dart_cache
```

결과는 `data/finance/dart_fhc/<종목코드>.json`에 씁니다. `--cache`는 회사 코드 목록(`corpCode.xml`)만 담으니 OneDrive 같은
동기화 폴더 밖에 두면 됩니다. 쓰는 API는 `corpCode.xml`, `fnlttSinglAcntAll`(전체 재무제표), `stockTotqySttus`(주식
총수), `list.json`(정기공시 목록, 정정 보고서의 원본을 찾을 때)입니다.

## `fhc` 계정 규칙

금융지주는 단일 포괄손익계산서(CIS)를 은행 형식으로 냅니다. 매출액이 없고 비용은 일반관리비입니다. 그래서 기존
`default` 규칙(매출액·영업이익)과 별도로 `fhc` 규칙을 두었습니다(`agentworld_lite/finance/dart.py`의
`FHC_ACCOUNT_RULES`). 계정은 XBRL `account_id`로 먼저 찾고, 후보가 여럿이면 적힌 순서대로 씁니다. 한글 계정명은
id가 없을 때만 쓰는 예비 수단입니다. 맞은 id는 보고서마다 `concepts`에 저장합니다. 이번 데이터셋의 연결 재무제표
24개는 모든 계정을 id로 찾았습니다.

| 계정 | 라벨 | XBRL `account_id` | 그룹별 차이 |
|---|---|---|---|
| `net_interest_income` | 순이자이익 | `ifrs-full_InterestRevenueExpense` | 네 그룹 같음(이름은 순이자이익·순이자손익) |
| `net_fee_income` | 순수수료이익 | `ifrs-full_FeeAndCommissionIncomeExpense` | 같음 |
| `general_admin_expense` | 일반관리비 | `ifrs-full_SellingGeneralAndAdministrativeExpense` | 신한지주만 `ifrs-full_GeneralAndAdministrativeExpense` |
| `credit_loss_provision` | 신용손실충당금 전입액 | `ifrs-full_ImpairmentLoss...DeterminedInAccordanceWithIFRS9` | 2025년 1분기 보고서는 KB·신한이 `dart_AdditionReversalOfCreditLossFinancialAssets`, 하나가 `ifrs-full_AdditionalAllowanceRecognisedInProfitOrLoss...` |
| `operating_income` | 영업이익 | `ifrs-full_ProfitLossFromOperatingActivities` | 같음 |
| `net_income` | 당기순이익(비지배지분 포함) | `ifrs-full_ProfitLoss` | 같음 |
| `net_income_owners` | 지배주주순이익 | `ifrs-full_ProfitLossAttributableToOwnersOfParent` | 하나는 이 행의 이름을 "반기순이익(손실)"로 적음 |
| `equity_owners` | 지배주주지분(신종자본증권 포함) | `ifrs-full_EquityAttributableToOwnersOfParent` | 같음 |
| `hybrid_capital` | 신종자본증권 | `dart_HybridBonds` | 같음 |
| `equity_total` | 자본총계 | `ifrs-full_Equity` | 같음 |
| `total_assets` | 자산총계 | `ifrs-full_Assets` | 같음 |

- **이름만으로는 틀린 행을 고를 수 있습니다.** 신한지주는 포괄손익의 지배주주 몫을 "지배기업 소유주지분"이라고
  적어 순이익 행과 이름이 같습니다. 그래서 이름으로 찾을 때 `ComprehensiveIncome`이 든 id나 "포괄"이 든 이름은 건너뜁니다.
- **누적.** 손익 항목은 분기·반기 보고서의 `thstrm_add_amount`(누적)를, 사업보고서는 `thstrm_amount`(연간)를 씁니다.
  2분기 단독 = 상반기 누적 − 1분기.
- **별도 재무제표(OFS)**에는 비지배지분이 없어서 `net_income_owners`와 `equity_owners`가 없습니다. 지주회사 별도 순이익은
  대부분 자회사 배당이라 연결 순이익과 크게 다릅니다(오답 함정으로 씀).

### 주식 수

`stockTotqySttus`의 `se` 값은 그룹마다 다릅니다("보통주", "의결권 있는 보통주", 줄바꿈이 든 "의결권 있는\n보통주").
그래서 공백을 모두 지우고 끝이 "보통주"·"우선주"인지로 구분하도록 고쳤습니다. 분기보고서는 이 표를 "-"로 비워 두는
경우가 많아(KB·하나·우리의 1·3분기, 신한지주의 2026년 1분기) 그런 보고서는 주식 수를 저장하지 않습니다. 반기·사업보고서에는 네 그룹 모두
있습니다. 네 그룹 모두 우선주가 없고(신한지주가 예전에 발행한 우선주는 모두 소멸해 "-"), 2025년 말보다 2026년 6월 말 발행
보통주식 수가 적습니다(자기주식 소각). KB금융: 381,462,103주 → 354,687,734주.

### 정정 보고서

정정 보고서가 있으면 DART는 정정본의 수치와 접수번호를 돌려줍니다. 데이터셋은 그 버전의 `rcept_no`와 접수일(`filed`)을
쓰고, `list.json`에서 찾은 원래 보고서를 `first_rcept_no`, `first_filed`로 함께 저장합니다. 기준일(as-of) 가드는 보수적으로
정정일을 씁니다. 이번 데이터에 들어간 정정은 모두 [기재정정]이며, 정정본의 재무 수치는 다음 보고서의 비교 기간 수치와
같습니다(정정 전후로 숫자가 바뀌지 않음).

| 그룹 | 보고서 | 최초 접수 | DART가 돌려준 정정본 |
|---|---|---|---|
| KB금융 | 2025년 1분기 | 2025-05-15 | 2025-05-26 |
| KB금융 | 2025년 반기 | 2025-08-14 | 2026-03-24 |
| KB금융 | 2025년 사업 | 2026-03-13 | 2026-06-19 |
| 신한지주 | 2025년 사업 | 2026-03-18 | 2026-10-07 |
| 신한지주 | 2026년 반기 | 2026-08-14 | 2026-10-07 |
| 하나금융지주 | 2025년 반기 | 2025-08-14 | 2025-08-14(같은 날 정정) |
| 하나금융지주 | 2025년 사업 | 2026-03-16 | 2026-08-14 |
| 우리금융지주 | 2025년 1분기 | 2025-05-15 | 2025-06-09 |

신한지주의 2026년 반기보고서가 2026-10-07에 정정되었기 때문에 과제의 기준일은 **2026-10-15**입니다. 2026년 3분기
보고서는 11월 중순에 나오므로 아직 없습니다.

## 데이터셋 (`data/finance/dart_fhc`)

그룹마다 2025년 1분기·상반기·3분기·연간, 2026년 1분기·상반기의 연결(CFS)·별도(OFS) 재무제표, 반기·사업보고서의 주식
수입니다. 연결 재무제표는 모두 11개 계정이 있습니다. 회계연도는 역년(12월 결산)입니다.

**실적 발표와 대조.** 과제 입력값을 각 그룹의 2026년 2분기 실적 발표(2026-07-23, 24, 잠정치)와 맞춰 보았습니다.

| 항목 | 그룹 발표 | 데이터셋 |
|---|---|---|
| KB금융 상반기 / 2분기 지배주주순이익 | 3조 8,846억 / 1조 9,922억 | 3,884,590 / 3,884,590 − 1,892,401 = 1,992,189 |
| KB금융 상반기 CIR | 36.2% | 3,654,401 / (5,440,114 + 3,654,401 + 1,013,014) = 36.16% |
| 신한지주 상반기 / 1분기 / 2분기 지배주주순이익 | 3조 4,427억 / 1조 6,226억 / 1조 8,201억 | 3,442,729 / 1,622,594 / 1,820,135 |
| 신한지주 1분기 CIR | 36.7% | 1,545,405 / (2,154,453 + 1,545,405 + 520,112) = 36.62% |
| 하나금융지주 상반기 / 2분기 순이익 | 2조 4,029억 / 1조 1,928억 (전년 2분기 1조 1,733억) | 2,402,868 / 1,192,823 (1,173,286) |
| 우리금융지주 상반기 / 1분기 / 2분기 순이익 | 1조 6,090억 / 6,044억 / 1조 46억 | 1,608,896 / 604,318 / 1,004,578 |
| 우리금융지주 상반기 순영업수익 | 5조 7,227억 | 2,123,827 + 2,632,856 + 966,025 = 5,722,708 |
| 우리금융지주 1분기 CIR | 50% 초과(희망퇴직 비용 반영) | 51.59% |

데이터셋 금액 단위는 백만원입니다. 우리금융지주의 상반기·1분기 순이익은 발표치와 억원 단위에서 1억원씩 차이가 나는데,
잠정치와 공시 원값의 반올림 차이로 보입니다. 우리금융지주가 발표한 순영업수익이 이 문서의 영업수익 정의(영업이익 +
일반관리비 + 신용손실충당금 전입액)와 정확히 같고, KB금융이 발표한 상반기 CIR도 같은 정의로 재현됩니다. 신한지주의
1분기 CIR은 0.1%p 차이가 나서, 그룹마다 CIR 분모를 조금씩 다르게 정의하는 것으로 보입니다. 과제는 정의를 문장으로
못 박아 둡니다. 총자산은 XBRL 원값이며 발표치와 대조하지 않았습니다.

### 주가를 쓰지 않는 이유

- **DART에는 주가가 없습니다.** 정기보고서와 OpenAPI에는 종가가 없습니다.
- **KRX Open API 약관이 재배포를 막습니다.** 인증키 방식의 자동 조회는 허용하지만, 이용약관 제11조 2항이 데이터를
  제3자에게 제공하는 것을 금지합니다. 공개 저장소와 공개 데모에 넣으면 제3자 제공이 되므로 쓰지 않았습니다. 사이트
  화면을 긁어 오는 방식도 쓰지 않습니다.
- **공시만으로 검증할 수 있어야 합니다.** 데스크의 모든 숫자는 사실 ID와 접수번호로 공시까지 거슬러 갈 수 있게
  만들었습니다. 출처 계약이 다른 시세를 섞으면 이 성질이 깨집니다.

그래서 저장소의 데이터셋에는 종가가 없고(`prices`가 비어 있음, 테스트가 확인), 과제는 BPS·ROE처럼 공시만으로 계산되는
지표를 씁니다. 밸류에이션 스냅샷은 코드로 남겨 두되 꺼 둡니다. 사용자가 자기 책임으로 출처가 있는 종가를 넣으면 켜집니다.

```bash
python -m agentworld_lite.finance fetch --source dart --profile fhc --company 105560 055550 086790 316140 --price 105560@2026-06-30=<종가> 055550@2026-06-30=<종가> 086790@2026-06-30=<종가> 316140@2026-06-30=<종가> --price-source "<출처, 조회일>"
```

`--periods` 없이 `--price`만 주면 재무제표를 다시 받지 않고 기존 파일에 종가만 더합니다. 출처가 없는 종가는 쓰지
않습니다. 그렇게 만든 데이터셋은 공개 저장소에 올리지 마세요.

## 과제 (`tasks/finance_kr`)

모두 공유형/개인형 쌍입니다. 방법론 문장이 공유 문서에 있거나 analyst의 개인 브리핑에만 있습니다. 기준일은
2026-10-15, 허용 오차는 0.1%입니다. 과제 지시문은 기존 한국 과제처럼 영어이고, 이름과 설명의 한국어판(`name_ko`,
`description_ko`)이 함께 있습니다. 금액 단위는 원이며 아래 표는 억원으로 적었습니다.

### k01: KB금융 BPS·TTM ROE (2026년 상반기 말)

공시만으로 계산하는 지표입니다.

- TTM 지배주주순이익 = 2025 연간 − 2025 상반기 + 2026 상반기 = 58,332 − 34,357 + 38,846 = **6조 2,821억원**.
- 보통주자본 = 지배주주지분 − 신종자본증권. 백만원 단위로 2026-06-30에 60,890,838 − 4,206,721 = 56,684,117,
  2025-06-30에 59,069,434 − 5,162,483 = 53,906,951(억원으로 566,841과 539,070).
- BPS = 2026-06-30 보통주자본 / 유통 보통주식 수(발행 354,687,734주 − 자기주식 1,300,000주 = 353,387,734주)
  = **160,402원**. KB금융에는 우선주가 없습니다.
- TTM ROE = TTM 지배주주순이익 / 평균 보통주자본((539,070 + 566,841) / 2 = 552,955) × 100 = **11.36%**. 반기·분기
  수치를 연환산하지 않습니다.

| 실수 | 결과 | 빗나가는 정도 |
|---|---|---|
| 비지배지분 포함 순이익 | 6조 3,210억원, ROE 11.43% | +0.62% |
| 직전 연간 순이익만, 상반기 × 2, 2분기 × 4 | | −7.1%, +24%, +27% |
| 별도 재무제표 순이익 | 2조 6,267억원 | −58% |
| 자기주식을 빼지 않은 주식 수 | BPS 159,814원 | −0.37% |
| 신종자본증권을 빼지 않음 | BPS 172,306원, ROE 10.47% | +7.4%, −7.8% |
| 자본총계(비지배지분 포함) | BPS 177,431원, ROE 10.16% | +10.6%, −10.6% |
| 2025년 말 주식 수 또는 자본 | BPS 158,076원, 154,756원 | −1.5%, −3.5% |
| 기말 자본만으로 ROE | 11.08% | −2.4% |
| 2025년 말과 2026년 6월 말의 평균 | 11.28% | −0.7% |
| 상반기 순이익 연환산 ROE | 13.95% | +23% |
| 비율을 소수로(0.1136) | | |

KB금융은 상반기 ROE를 14.09%로 발표했는데(연환산, 회사 정의의 평균자본), 정의가 달라 TTM ROE와 맞춰 보지 않았습니다.
같은 방식(상반기 연환산, 2025년 말과 2026년 6월 말 보통주자본 평균)으로 계산하면 13.95%로 발표치에 가깝습니다. KB금융에는
우선주가 없으므로 우선주 함정 대신 보통주가 아닌 자본인 신종자본증권을 함정으로 썼습니다. 정답 시나리오는 18라운드 중
10라운드에 풉니다.

### k02: 신한지주·우리금융지주 2026년 2분기 영업이익경비율(CIR)

2분기 단독 = 상반기 − 1분기. CIR = 일반관리비 / 총영업이익 × 100, 총영업이익 = 영업이익 + 일반관리비 + 신용손실충당금
전입액.

| | 2분기 일반관리비 | 2분기 총영업이익 | 정답 CIR | 상반기 누적 | 1분기 | 순이자+순수수료 분모 | 영업이익+일반관리비 분모 |
|---|---|---|---|---|---|---|---|
| 신한지주 | 16,707 | 46,033 | **36.29%** | 36.45% (+0.4%) | 36.62% (+0.9%) | 38.64% (+6.5%) | 40.29% (+11%) |
| 우리금융지주 | 12,100 | 29,650 | **40.81%** | 46.01% (+13%) | 51.59% (+26%) | 39.56% (−3.1%) | 47.91% (+17%) |

비율을 소수(0.3629)로 내거나 두 그룹을 바꿔 내도 오답입니다. 하나금융지주는 2분기·상반기·1분기 CIR이 38.89%,
38.89%, 38.88%로 거의 같아서 누적 함정이 허용 오차 안에 들어가므로 이 과제에서 뺐습니다. KB금융은 36.89%입니다.

### k03: 4대 금융지주 2026년 2분기 지배주주순이익과 보통주자본

답 6개: 그룹별 2분기 단독 지배주주순이익과, 2026-06-30 보통주자본(지배주주지분 − 신종자본증권)의 단순 평균·최대−최소 차.

| 그룹 | 2분기 지배주주순이익 | 보통주자본 |
|---|---|---|
| KB금융 | 38,846 − 18,924 = **19,922** | 566,841 |
| 신한지주 | 34,427 − 16,226 = **18,201** | 559,369 |
| 하나금융지주 | 24,029 − 12,100 = **11,928** | 424,773 |
| 우리금융지주 | 16,089 − 6,043 = **10,046** | 353,736 |

보통주자본 평균 **47조 6,180억원**, 최대−최소 차 566,841 − 353,736 = **21조 3,105억원**(KB금융 − 우리금융지주).

| 실수 | 빗나가는 정도 |
|---|---|
| 비지배지분 포함 순이익 | +0.9%(KB) ~ +2.2%(우리) |
| 상반기 누적, 상반기 ÷ 2 | +60%(우리) ~ +101%(하나), −20%(우리) ~ +0.7%(하나) |
| 1분기, 전년 2분기, 2025년 4분기 | −40% ~ +1.4%, −15% ~ −1.6%, −52% ~ −72% |
| 신종자본증권을 빼지 않음 | 평균 +8.5%, 차 +3.3% |
| 자본총계 | 평균 +12.6%, 차 +2.4% |
| 3월 말·2025년 말 잔액 | 평균 −2.8%·−5.2%, 차 −0.9%·+5.0% |
| 중앙값, 세 그룹 평균 | +3.3%, +8.6% |

정답 시나리오는 k01을 18라운드 중 10라운드, k02를 20라운드 중 11라운드, k03을 30라운드 중 15라운드(collector 9번
조회·공유, analyst 6번 계산)에 풉니다. 아무것도 하지 않는 팀은 모두 실패합니다. 어느 과제도 주가를 쓰지 않습니다.

## 섹터 노트 (한국어)

```bash
python -m agentworld_lite.finance note --reference k03_big4_q2_sector_shared --out reports/k03_note_ko.md
```

`agentworld_lite/finance/note_ko.py`는 끝난 궤적을 모델 호출 없이 한국어 마크다운 노트로 만듭니다. 캐나다 노트와 같이
기록된 행동을 새 데스크에서 다시 실행해 사실과 출처를 되살리고, 기록된 제출과 다르면 노트를 내지 않습니다. 숫자마다
사실 ID와 DART 접수번호가 붙고, 계산한 숫자는 계산에 들어간 보고서의 접수번호를 모두 적습니다.

```
1조 9,922억원 [사실 `calc.kb_q2_net_income_owners` · 접수번호 20260814004200, 20260515002888]
```

그룹 비교표, 섹터 전체 답, 답마다 공시 원값까지 내려가는 계산 과정, 출처 표(보고서, 접수번호 링크, 접수일, 최초
접수)가 들어갑니다. 내보내기 전에 원·억원·조, 배, %, %p, 주 단위가 붙었거나 천 단위 구분 기호·소수점이 있는 숫자 뒤에
인용이 있는지 스스로 검사합니다(`uncited_figures_ko`). `note` 명령은 과제가 한국 금융지주 데스크이면 자동으로 한국어
노트를 씁니다.

## 밸류에이션 스냅샷 (한국어, 기본으로 꺼짐)

```bash
python -m agentworld_lite.finance valuation --market kr --bank KB금융 --out reports/kb_valuation_ko.md
```

`agentworld_lite/finance/valuation_ko.py`는 한 그룹을 나머지 세 그룹과 한 쪽에 비교합니다. 2026-06-30 종가, 시가총액,
TTM 지배주주순이익, PER, 보통주자본, PBR, 단순 ROE(TTM 지배주주순이익 / 기말 보통주자본), 2분기 CIR을 보여 주고, 은행주는
P/B와 ROE로 비교하므로 나머지 세 그룹 PBR의 최저·중간값·최고에 대상 그룹의 보통주자본을 곱한 시가총액 범위를 적습니다.
맨 위에 **교육용 예시·투자 권유 아님** 문구가 있고, 투자의견과 목표주가는 없으며, 범위는 적정가치나 예측이 아니라고
적습니다. 숫자마다 사실 ID와 접수번호가 붙고, 종가는 넣을 때 적은 출처를 인용합니다.

저장소에는 종가가 없으므로 이 명령은 지금 "DART 공시만 쓰고 주가를 싣지 않는다"는 오류와 함께 멈춥니다. 네 그룹 모두에
출처가 있는 종가를 넣어야 켜집니다(위 "주가를 쓰지 않는 이유"). 계산과 인용은 테스트가 임시 폴더의 가상 가격으로
확인합니다. 그 가격은 시장 가격이 아니며 저장소에 남지 않습니다.

## 데모

```bash
streamlit run demo/streamlit_app_kr.py
```

한국어 Streamlit 앱입니다. 모델, API 키, 네트워크를 쓰지 않습니다. 화면은 네 개입니다.

- **데스크 재생.** 정답 시나리오나 올린 궤적을 라운드 슬라이더로 봅니다. 에이전트별 행동, 노트북 크기, 허용 오차 안의 답.
- **4대 금융지주 데이터.** 2분기 지배주주순이익, 2분기 CIR, 보통주자본 차트와, 숫자마다 보고서와 접수번호를 적은 표.
- **섹터 노트.** 위 노트와 마크다운 내려받기.
- **과제.** 설명, 역할과 도구 제한, 방법론 위치(공유 문서 또는 개인 브리핑), 정답.

주가가 없으므로 밸류에이션 화면은 목록에 없고, 사이드바에 그 이유를 적었습니다. 네 그룹 모두에 출처가 있는 종가를
넣으면 다섯 번째 화면으로 나타납니다. 쿼리 파라미터로 상태를 열 수 있습니다
(`?task=k03_big4_q2_sector_shared&view=데스크 재생&round=9`).
배포는 하지 않았습니다. Streamlit Community Cloud에 올릴 때는 main file을 `demo/streamlit_app_kr.py`로 정하고
`demo/requirements.txt`를 그대로 쓰면 됩니다.

## 실행

```bash
python -m agentworld_lite.finance make-tasks
```

```bash
python -m agentworld_lite.finance validate --tasks tasks/finance tasks/finance_ca tasks/finance_kr
```

```bash
python -m agentworld_lite.finance run --tasks tasks/finance_kr --agent llm --model claude-opus-5-5 --settings full no_comm --seeds 7 8 9 --out runs/finance_kr_opus
```

한국 금융지주 데스크는 자체 시스템 프롬프트와 도구 설명을 씁니다(금융지주 계정, 신종자본증권, 정정 보고서). 모델
실행은 아직 하지 않았습니다.

## 한계

- 수치는 DART가 돌려준 최신 정정본입니다. 정정으로 숫자가 바뀌었다면 원래 값은 남지 않습니다(이번 데이터에서는 바뀐
  숫자가 없음을 비교 기간 수치로 확인했습니다).
- 계정은 id로 찾습니다. 그룹이 계정 id를 바꾸면 `FHC_ACCOUNT_RULES`에 후보를 더해야 하며, 저장된 `concepts`가 무엇이
  맞았는지 보여 줍니다.
- 최초 접수를 찾는 규칙은 12월 결산 회사의 보고서 이름("반기보고서 (2026.06)")에 맞춰져 있습니다.
- 지배주주순이익에는 신종자본증권 배당 몫이 들어 있어 보통주 기준 순이익보다 조금 큽니다. 공시 재무제표에 보통주
  귀속 순이익 계정이 없어 그대로 둡니다.
- 일회성 항목(희망퇴직 비용, 보험 계리가정 변경, 충당금 환입 등)은 조정하지 않습니다.
- CET1 비율과 위험가중자산은 DART 재무제표에 없어(바젤 공시는 별도) 넣지 않았습니다.
