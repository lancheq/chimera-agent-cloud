# CHIMERA-agent experiment inventory (for a 6-page MICCAI LNCS methods paper)

Sources: `docs/EXPERIMENTS.md` (1283 lines, exp-000…exp-027) and
`docs/CHIMERA-agent_提升手段全览_20260912.md` (366 lines, codes M1–M7 / R1–R6 / T3-1…T3-3), cited as `提升:N`.
Supplementary, labelled: `reports/pseudo_data_honest_cv_20260914.md`, `reports/t2_interval_mismatch_evidence_20260913.md`.
Nothing invented; ambiguous numbers are quoted verbatim. Line numbers as read.

## A. Inventory

| id | date | changed / compared | dataset & n (task, cases, GT?) | metric(s) & value | verdict / superseded? | evidence |
|---|---|---|---|---|---|---|
| exp-000 | 08-11 | [deploy] cloud deploy (venv, llama.cpp-CUDA, models) | mock 3×1, no GT | 5.88 MB/s download; GPU 23 GB free | neutral/deploy | EXPERIMENTS.md:116-191 |
| exp-001 | 08-11 | Qwen3.6-35B-A3B Q4 server, 3 fixes (CUDA, OOM, flash_attn) | service | GPU 21708/23028 MiB (94 %) | positive; server later replaced (exp-027) | :193-239 |
| exp-002 | 08-11 | smoke T1: embedding→RAG→LLM→form_fill | mock T1 1 case, no GT | ~15 s agent, ~52 s total | positive (pipeline) | :241-283 |
| exp-003 | 08-11 | full mock run, 3 tasks | mock 3 cases, no GT | 3/3 outputs, ~70 s, 1 parse warning | positive | :285-308 |
| exp-004 | 08-11 | official upload + full run start | official 195/153/75=423 (+mock=425), partial GT | upload 8.3 MB, sha256 `e3e2385e…` | infra; superseded | :310-338 |
| exp-005 | 08-12 | instance rebuild, resume run | 92 T1 done; GT subsets | resume verified (T2-001 ~90 s) | recovery; superseded | :341-360 |
| exp-006 | 08-12 | first LLM-only full baseline | T1 195 (91 GT), T2 154 (72 GT), T3 76 (75 GT) | T1 Acc 69.2 %, F1 0.794 (26 FP/2 FN); T2 Acc 59.7 %, Macro-F1 0.408 (WW F1 0.000); T3 EventAcc 45.3 %, C-index 0.6186, MAE 17.4 mo; conf. match 54–60 %; weight match 32–37 % | negative baseline; historical | :363-397 |
| exp-007 | 08-13 | P0 prompt calibration (thresholds, confidence) | 425 cases, GT subsets | targets T1 75 %+, T2 65 %+, T3 55 %+; no result here | neutral; outcome in exp-010 | :399-449 |
| exp-008 | 08-14 | deterministic predictor vs LLM; vector ablation | 91/72/75 GT, 5-fold CV | T1 0.681 vs 0.692; T2 Acc 0.736 / F1 0.643 vs 0.597/0.408; T3 C-index 0.714 / EventAcc 0.653 vs 0.619/0.453; vectors hurt T1/T2 (0.670→0.561), help T3 (`VECTOR_TASKS={3}`); 425 cases in 5.7 s vs ~3 h | positive T2/T3 | :452-497 |
| exp-009 | 08-14 | predictor wired in (`decision_override`, state, prompt) | stub tests, 3 tasks | assertions pass | positive integration | :500-529 |
| exp-010 | 08-14 | P0 vs baseline; hybrid decision-source choice | 425, GT subsets; hybrid on GT | P0 T1 0.681/0.756, T2 0.653/0.487, T3 C-index 0.281 / EventAcc 0.293 (collapse). Hybrid full-fit: T2 0.847, T3 EventAcc 1.000, C-index 0.922 | T2 positive; **T3 P0 negative**; hybrid = optimistic, later retracted | :532-573 |
| exp-011 | 08-14 | interruption forensics; 3 parallel processes | ops | SIGHUP root cause; 3 PIDs raced | neutral (lesson) | :575-611 |
| exp-012 | 08-14 | integrated full eval (T1 LLM + T2/3 override) | 425, GT subsets | T1 0.681/0.756; T2 Acc 0.847, Macro-F1 0.800 (+0.392); T3 EventAcc 1.000 (optimistic), C-index 0.550 (−0.069); months pred 21.6 vs GT 39.2, MAE 12.72 | mixed; T3 numbers retracted (exp-026) | :614-663 |
| exp-013 | 08-14 | completeness check of override run | 425 (195/154/76), 0 fail | T1 yes 138/no 57; T2 AT 72/AS 44/CS 27/WW 11; T3 event 56/20; months 12–60, mean 21.6 | neutral | :666-744 |
| exp-014 | 08-15 | official evaluate.py + GC-format converter | sample 2/task, GT mismatch unknown | DAG 0.582 / 0.238 / 0.315 | invalid inputs; superseded | :746-775 |
| exp-015 | 08-15 | 425-case quality diagnosis | 425; GT 91/72/75 | reveal empty 425/425 (100 %) → grounding zeroed, "损失 ~17.5 % case_score"; `called_tools=[]` 402/402; T1 "clear" acc 81 %; T3 months constant 12.0 (GT 5–46) | negative (3 defects); 17.5 % is an estimate | :778-804 |
| exp-016 | 08-15 | post-hoc reveal synthesis (decisive vs important) | 425 (312=73 % filled); eval on 6 bad samples | T1 DAG 0.507→0.565, grounding 0.292→0.792; T2 0.235→0.264, grounding 0.333→0.833; tool 1.000 | positive mechanism; sample eval invalid | :807-840 |
| exp-017 | 08-15 | official 6-case end-to-end hybrid | 6 sample cases, GT mismatched | T1 0.565, T2 0.264, T3 0.815, mean 0.548; T2_01 0.000; T3 months 12 vs GT 5/46 | **RETRACTED by exp-018** | :844-890, 894-931 |
| exp-018 | 08-16 | audit sample inputs vs GT | 6 cases | BX_01≡BX_02 (psa 5.2, pirads 4); T2_01≡T2_02 (psa 187, pirads 5, isup 4); T2_01 GT is another patient (psa 4.7, pirads 2, isup 1) | **retraction**: sample = format sample; "P0 T2 fix" cancelled | :894-931 |
| exp-019 | 08-16 | Pro 4-stage graph + self_refine + fact_check + inline reveal | 6 invalid sample cases | reveal T1+T2 0/4→4/4; 2 refine rounds/case; ~8 min/case; 0 errors | positive mechanism; GT metrics not citable | :933-1005 |
| exp-020 | 08-17 | Pro full run, override disabled | T1 5/195, T2 154/154, T3 0 at log time | T2 154 cases 0 fail; ~8 min/case | progress; superseded | :1009-1045 |
| exp-021 | 08-17 | T3 bug fixes (reveal fallback; months=0) | T3 6-case verify + 2 GT cases | reveal 0/13→6/6; months=0 10/13→0/6; months [0,0,0,6,0…]→[12,12,24,30,36]; DAG 0.815→0.630; event acc 1.000→0.500; time 0.760 flat; grounding 0→1.0 | fixes positive; DAG drop from bad T3-006 input | :1048-1112 |
| exp-022 | 08-17 | T2 Pro full official eval | T2 154 (no per-case GT); 2 GT cases | DAG 0.264→0.294, grounding 0.833→1.000, tool 1.000; decision acc 0.500; AT 50.6/AS 24.7/CS 16.2/WW 8.4 %; borderline 70.8 %; reveal 154/154; free_text mean 227 chars | mixed; `called_tools` still 0 % ("假象") | :1115-1148 |
| exp-023 | 08-18 | mandatory MCP calls inside plan node | 6-case smoke (3/6 done) | `called_tools` 0 %→3/3=100 %; ~2 s/tool; 0 violations | positive mechanism; partial | :1151-1217 |
| exp-024 | 08-18 | T1 Pro full run with MCP | 197 cases (195+2 mock), 91 GT | 193/197=98 %; 4 fails (2 timeout ~950 s, 2 parse); called_tools 190/190; reveal 193/193; yes 158/35 (81.8/18.2 %) vs 70.8 % before; clear 153/40 | positive + caution (yes rate up) | :1220-1283 |
| exp-025 | 08-22 | [deploy] official GEval judge gemma4:e4b Q4_K_M | service | 4746 MB; 4/43 layers on GPU (~550 MB) | deploy; not yet used for scored runs | :50-78 |
| exp-026 | 08-23 | LOOCV vs optimistic full-fit | LOOCV n=91/72/73, GT; T3 threshold fixed 0.5 | T1 0.637 (CV 0.681, optimistic 0.769); T2 0.653 (0.736, 0.847); T3 EventAcc 0.726 (0.653, 1.000; majority 0.740), C-index 0.664 (0.714, 0.922); T2 confusion AT 21/31, AS 13/25, CS 13/14, WW 0/2 | **negative on optimistic bounds** ("确认乐观上界存在训练泄漏") | :81-113 |
| exp-027 | 08-23 | custom server dropped `extra_body` → thinking in content; switch to official server | GT v3 rerun, 236 cases, `override_tasks=[3]` | failure storm → `done=14, FAILED=0`; ~271 s/case; T2 override cancelled | positive root cause; partial | :9-47 |
| 提升 §3.1 | 09-12 | platform run, v8 bundle | **109 held-out cases** (T1 50/T2 36/T3 23), official GT | overall **0.6063**; T1 0.7105 / acc 0.840 / F1 0.810; T2 0.5283 / 0.639 / wF1 0.616; T3 0.5537 / event 0.739 / time 0.445; gate 88 pass, 13 treatment fail, 8 biopsy fail | **headline**; md5 `47cb147f…` | 提升:67-75,334; 交接文档:122-128; GC日志:2716-2719 |
| 提升 §3.2 | 09-12 | honest T2 CV baseline, 3 heads | T2 72 GT, 5-fold | LR Acc 0.679±0.063, F1 0.580±0.067; MLP 0.666±0.097, F1 0.627±0.095; HistGB 0.554±0.062, F1 0.422±0.092; majority 0.431 | positive reference; platform 0.639 in interval | 提升:77-86 |
| 提升 §3.3 | 09-12 | reasoning agreement with official expected reasoning | T1 58, T2 60, local | conf. agreement T1 0.5345 (κ 0.0095), T2 0.3333 (κ −0.0453); weights T1 0.3541 (κ 0.1351), T2 0.3494 (κ 0.1019); our T2 conf. clear 22/uncertain 18/borderline 20 vs expected 52/8 | negative; κ ≠ platform `confidence_weighted_kappa` | 提升:88-101 |
| 提升 M1 | 09-12 | class-weight / threshold calibration for AT recall (in-fold) | T2 72 GT, CV replay | LR Acc 0.679→0.693, F1 0.580→0.590, AT recall 0.705→0.738; MLP recall 0.738→0.905 but F1 0.627→0.608 | **rejected: 噪声内** (≈1 case vs ±0.063) | 提升:109-111,307 |
| 提升 M2 | 09-12 | feature profile (NaN + univariate AUC for AT) | T2 72 GT | `path_isup` 0.862, `path_gleason_sec` 0.816, `path_gleason_prim` 0.786, `pirads_max` 0.721, `psa_trend_last` 0.674; weak `free_psa_ratio` 0.419, `zone_*` 0.42–0.52 | positive descriptive (`psa_trend_slope` 0.633) | 提升:112,121 |
| 提升 M3 | 09-12 | are 3 all-NaN T2 features a bug? | T2 field audit | T2 has 6 fields; `path_svi/epe/margin_pos` are post-op (T3-only) | **rejected**: correct empty features | 提升:113,306 |
| 提升 R1 | 09-12 | did `_calibrate_uncertain` (`UNVERIFIED>2`) cause downgrades? | T1/T2 local | fired T1 2×/T2 3×; removing: T2 agreement 0.3333→0.3833 (κ −0.045→−0.037), T1 0.5345→0.5690 | **rejected**: negligible | 提升:132,304 |
| 提升 R2/R4 | 09-12 | did override flips cause inconsistent reasoning? | T2 60 local | 1/60 flipped (T2-043, 0 warnings); mismatch in 68 % of the 59 non-flipped | **rejected**: no coupling | 提升:133,143 |
| 提升 R3 | 09-12 | atomic warning prevalence (`important` not mentioned) | T2 60 local | 11/60 cases, 26 warnings | exists, **not locally fixable** | 提升:134,136-137 |
| 提升 §6.1 | 09-12 | platform T3 breakdown | T3 23 held-out | time 0.4450; time-dep AUC 12 m 0.976 / 24 m 0.354 / 36 m 0.539 / 60 m 0.630; event1 time MAE **33.8 mo**; rationale 0.491 (lowest) | negative (time weakest) | 提升:156-170 |
| 提升 §6.2 | 09-12 | gate accounting on 109 cases | 50/36/23 | 21/109 gate fails (19.3 %) each case_score=0; reconstruction (0.7280×42)/50=0.6116 ✓, (0.6889×23)/36=0.4402 ✓; counterfactual overall 0.5445→0.6801 (+0.1356); T2 +0.2488, T1 +0.1165 | positive; counterfactual marked "≈" | 提升:207-244 |
| 提升 §6.2b | 09-12 | leaderboard verification, rank-density math | 122 entries | #111 `0.6063` vs #112 `0.6029` (Δ0.0034 ≈0.37 cases; 1 case = 0.00917) | positive; earlier "110 vs 75" superseded | 提升:246-270 |
| 提升 §7-1/2 | 09-12 | assumptions re-checked on real platform data | 109 cases | `tool_score` 0.9969 (not 0.5 from 3 cases); missing-components-zeroed only 2/109 | **rejected assumptions** | 提升:299-300 |
| 提升 §7-3 | 09-12 | retraction of "local T2 0.967 ⇒ OOD drift" | T2 60 comparable | training-set 0.967 (pipeline) / 0.983 (predictor); honest CV 0.666–0.679; platform 0.639 | **RETRACTED** (method error) | 提升:39-41,301; GC日志:2908-2991 |
| suppl. pseudo-CV | 09-14 | pseudo-label augmentation after fixing CV leak | T2 72 GT + 68–80 pseudo, 5-fold | leak fingerprint: majority 0.431→0.553; fixed: baseline F1 0.627; `--include-pseudo` 0.571; `--include-all` 0.649 (HistGB 0.679/0.649) = +0.022 vs σ ±0.113; MLP 0.627→0.526 | **rejected**: noise, sign flips per head; pre-fix 0.815/0.757 void | pseudo_data_honest_cv:11,55,87-101 |
| suppl. interval | 09-13 | eval-interval vs local labels; override attribution | platform 109 vs local GT | T1 intervals disjoint (50 vs 91), T2 `T2-155..225` vs `001..153` = 0 overlap, T3 `T3-077..099` vs `001..075` = 0; T1 (no override) 0.8400 > T2 (override) 0.6389, T3 event 0.7391; T2 AT recall 0.250 (4/16), 29/36=81 % surveillance; T2 gate tool/grounding 1.0000/1.0000; case_score 0.4402→~0.5545, overall 0.6063→~0.615 (optimistic) | positive; causal override claim "不是结论" | t2_interval_mismatch:14-27,41-45,58-93 |

## 1. Headline numbers (best provenance)

1. **Platform overall 0.6063**, v8 (md5 `47cb147f3b9e5e5f09df0933ab7b9960`), 109 held-out cases (50/36/23): T1 0.7105 (acc 0.840, F1 0.8095), T2 0.5283 (acc 0.6389, wF1 0.6164), T3 0.5537 (event 0.7391, time 0.4450) — 提升:70-75,334; platform block GC日志:2716-2719; table 交接文档:126-128.
2. **Honest T2 CV**: LR Acc 0.679±0.063, MLP Macro-F1 0.627±0.095, majority 0.431; platform 0.639 inside interval — 提升:77-86.
3. **Predictor > LLM at T2/T3 (5-fold CV)**: T2 Acc 0.736 / F1 0.643 vs 0.597/0.408; T3 C-index 0.714 / EventAcc 0.653 vs 0.619/0.453 — EXPERIMENTS.md:474-481.
4. **Contract layer near-perfect**: tool_score 0.9969 over 109 cases; T2 gate-pass tool/grounding 1.0000/1.0000 — 提升:299; t2_interval_mismatch:74-77.
5. **Post-hoc reveal synthesis** (6-case sample): T1 grounding 0.292→0.792, T2 0.333→0.833, tool 1.000 — EXPERIMENTS.md:827-835 (sample-based caveat).
6. **Mechanistic fixes on real runs**: reveal non-empty 0→100 %; called_tools 0→100 % — EXPERIMENTS.md:995,1187,1267-1268.
7. **Gate accounting**: 21/109 fails (19.3 %) each 0; verified reconstructions 0.6116 (T1) and 0.4402 (T2) — 提升:214-223.

Only these have a defined denominator and unambiguous provenance; anything marked "optimistic" must not be quoted as generalisation.

## 2. Negative results / rejected ideas

| idea | rejection reason | evidence |
|---|---|---|
| P0 prompt calibration for T3 | over-correction: C-index 0.619→0.281, EventAcc 0.453→0.293 | EXPERIMENTS.md:539-543 |
| Threshold / class-weight tuning (AT recall) | +0.010 Macro-F1, ≈1 case, «噪声内» vs ±0.063 | 提升:111,307 |
| `UNVERIFIED>2` as main loss cause | fires 2–3×; agreement only 0.3333→0.3833 | 提升:132,304 |
| Override flips causing contradictions | 1/60 flipped; 68 % of non-flipped also mismatch | 提升:133 |
| 3 NaN T2 features = parse bug | T2 has no post-op fields (correct empties) | 提升:113 |
| "tool_score is the bottleneck" (3 cases) | real value 0.9969 (109 cases) | 提升:299 |
| "Local 0.967 ⇒ OOD drift" | training-set accuracy; honest CV 0.666–0.679 | 提升:39-41,301 |
| Optimistic full-fit bounds (0.769/0.847/1.000/0.922) | LOOCV 0.637/0.653/0.726/0.664 shows leakage | EXPERIMENTS.md:107-112 |
| T2 predictor override (after selector flip-flop) | one wrong gate decision → case_score 0 (T2_01) | EXPERIMENTS.md:865,884 (inference itself rejected in exp-018) |
| Pseudo-label augmentation | +0.022 < σ 0.113; sign flips per head; 74–84 % AT bias | pseudo_data_honest_cv:96-101 |
| SFT on 35B; synthetic-variant augmentation; synthetic few-shot/RAG; T3 pseudo-labels | cost vs non-verifiability | pseudo_data_honest_cv:115-122 |
| Local-only fixes of atomic warnings (R6) | no eval-interval samples; risk > benefit | 提升:136-137,323 |

## 3. Retracted or superseded numbers

| number | status | reason |
|---|---|---|
| exp-017 DAG 0.565 / 0.264 / **0.815**, "T2_01 predictor wrong" | **retracted (exp-018)** | sample inputs: BX_01≡BX_02, T2_01≡T2_02; GT belongs to other patients (:894-931) |
| T3 EventAcc 1.000 and C-index 0.922 (exp-010/012) | retracted/relabelled | fitted on the same GT cases; LOOCV 0.726 / 0.664 (:645-649,107-112) |
| T1 0.769 / T2 0.847 optimistic full-fit | retracted | leakage; honest 0.681/0.736, LOOCV 0.637/0.653 (:96-112) |
| T2 local 0.967 (pipeline) / 0.983 (predictor), OOD-drift conclusion | **retracted 09-12** | training-set accuracy; replaced by CV 0.666–0.679 (提升:301; GC日志:2908-2991) |
| exp-014 sample DAG 0.582/0.238/0.315 | invalid | same bad sample; only 2 cases/task (:762-766) |
| exp-019/021 DAG 0.815 / 0.630 | not citable | same mismatched sample cases (:1087-1104) |
| "0.627→0.757 with pseudo-data", "HistGB 0.815/F1 0.757" | **void** | pseudo rows in eval folds; majority moved 0.431→0.553 (pseudo:38-57,94) |
| exp-012 T3 C-index 0.550 | superseded | platform gives T3 time 0.445 / event 0.739 (提升:73,159) |
| "110 vs 75" rank comparison | superseded | two different snapshots; same-moment #111 0.6063 vs #112 0.6029 (提升:246-257) |

Internal inconsistency to avoid: official cohort is **195/153/75 = 423** (:330) while runs report **425** (195/154/76 = official + mocks, :331,689,1248). Do not cite 425 as the official size.

## 4. Gaps

1. **No externally validated score for the Pro architecture** (4-stage graph, self_refine, fact_check, MCP, inline reveal). All its evidence is mechanical (non-empty fields, `called_tools`, retries) or from the invalid 6-case sample; the only scored submission is v8 (0.6063). No v9/v10 platform result in either target file.
2. **No honest held-out component ablation** (full Pro vs minus self_refine / fact_check / MCP) on one GT case set.
3. **No retrieval metric** for the ReflectiveRAG part; only guidelines DB size (1501 chunks, :355).
4. **No uncertainty for sampled numbers**: 2- and 6-case DAG values (0.582/0.238/0.315/0.264/0.294/0.815/0.630) have no CI and rest on known-bad inputs.
5. **No survival/time model**: no Cox or DeepSurv result; only the prospective criterion "c_index 基线 0.6186" and untried T3-1/T3-2 (提升:168-169); no local T3 time-dependent-AUC reproduction.
6. **No platform score for the calibrated/weight-aligned versions**; the +0.025~+0.050 overall figure is an extrapolation (提升:203,244; 冲刺方案:40).
7. **No published-baseline or inter-reader comparison, no external cohort**; only in-competition leaderboard numbers (0.6063 / 0.6029 / 0.6023, 提升:250-253).
8. **No foundation-model/PathChat baseline results** in these files (a proposal file exists: `docs/CHIMERA-agent公开数据集与PathChat方案.md`).
9. **No own imaging model**: images enter only as frozen modality-pooled vectors (MRI 1×1024, biopsy/prostatectomy 1–3×960) + PCA (:462).
10. **Predictor bundle provenance unverified** (task1/2 dated 9-05, task3 9-07; M7 never executed, 提升:122,319).
11. **`confidence_weighted_kappa` semantics** only quoted indirectly (T1 0.1935, T2 −0.1561, 提升:97); no local derivation.
12. **Ambiguous values**: "T3 多数类 EventAcc=0.740" vs event prevalence 56/76=0.737 (:105,723); LOOCV T3 n=73 (:95) vs 75–76 GT elsewhere; "损失 ~17.5 % case_score" (:794); T1 F1 both 0.794 (exp-006) and 0.8095 platform (提升:71).
