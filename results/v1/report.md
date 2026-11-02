# LEXI report: v1

LEXI-verified: **no** (every MUST row has to pass)

| id | class | criterion | target | observed | |
|---|---|---|---|---|---|
| RC1 | MUST | Test CP95 lower bound <= alpha for every active RG0 certificate; at most 1 of 12 cells may fail | <= 1 | 0 | pass |
| RC2 | MUST | Scorer calibration ECE on Valid | <= 0.02 | 4.928536224821417e-07 | pass |
| RC3 | TARGET | Median wrong autonomous commits between onset and ALERT/REVOKED at ~25% post-shift risk | <= 8 | None | not measured |
| RC4 | MUST | S6 null: exact 95% lower bound of false-REVOKE rate per 100,000 labeled commits; false-ALERT <= 1 per 10,000; no REVOKE in the live soak | <= 1 | None | not measured |
| RC5 | MUST | Median ALERT delay (labeled commits) <= 700 @4%, 200 @6%, 70 @10%, 30 @25% (1,000 runs each) | == True | True | pass |
| RC6 | MUST | Hallucinated values in ActionProposals over 100,000 random EBs | == 0 | 0 | pass |
| RC7 | MUST | 1,000 sampled decisions replay bit-identically | == 0 | 0 | pass |
| RC8 | MUST | Audit chain verifies; a one-byte flip fails verification | == True | True | pass |
| RC9 | MUST | Ledger invariants under kill -9 cycles, duplicate delivery and receipt-failure injection | == 0 | 0 | pass |
| RC10 | TARGET | T1 calls committed (direct or after <= 1 probe) in Q1 cells | >= 0.6 | 0.6564885496183206 | pass |
| RC11 | TARGET | Wrong-commit rate at matched autonomy vs B1, 95% interval excluding 1.0 | <= 0.5 | 0.2280544912728821 | pass |
| RC12 | TARGET | Mean probes per committed call; escalation rate <= 20% on RTP | <= 0.8 | 0.2835249042145594 | pass |
| L1 | MUST | EB complete to Decision, excluding audit fsync; p50 <= 3 ms | <= 10 | 1.2811832800036853 | pass |
| L2 | MUST | C3 lattice build, <= 40 tokens | <= 15 | 2.8772997800069775 | pass |
| L3 | MUST | Audit ack p99; ledger PREPARE p99 <= 8 ms (given V11) | <= 5 | 328.4944308800099 | FAIL |
| L4 | TARGET | EOU to first TTS audio byte, fast path; Evon path p95 <= 1.6 s | <= 1.0 | None | not measured |
| L5 | MUST | Sentinel update; transition to registry effect <= 100 ms; no torn snapshot | <= 1 | None | not measured |
| L6 | TARGET | Barge-in cancel | <= 150 | None | not measured |
| AV1 | MUST | Zero autonomous commits while any F1-F16 fault is active | == 0 | None | not measured |
| AV2 | MUST | Stop C10 -> zero autonomous commits within 10 s | == 0 | 0 | pass |
| AV3 | TARGET | 20 concurrent sessions: no SLO regresses by more than 10% | <= 0.1 | None | not measured |
| CS1 | TARGET | Evon on <= 35% of turns; credits per call reported | <= 0.35 | None | not measured |

## run

- Test calls: 1167
- Valid calls: 699
- registry: v3, scorer sc-112bbc2559
- seed: 20261004

## certificates

- cells certified: 5 of 12
- cells failing on Test: 0
- ECE on Valid: 0.0
- ECE on Test, stage 0: 0.045

## autonomy

- autonomy rate: 0.4473007712082262
- wrong-commit rate: 0.0211 [0.0106, 0.0374]
- commits: 522
- wrong commits: 11
- silent-error interception: 0.9415
- probes per commit: 0.284
- probe resolution rate: 0.382
- escalation rate: 0.553
- CELB-cap (bound): ₹951,250
- CELB-est (estimate): ₹101,108
- amount-weighted wrong-post rate: 0.0219
- largest single error: ₹17,670

## baselines

- B1 wrong-commit rate at LEXI's autonomy: 0.0924
- B1 autonomy: 0.417
- LEXI / B1 wrong-commit ratio: 0.23 [0.10, 0.38]
- B1 autonomy at LEXI's risk: 0.027
- B2 wrong-commit rate at LEXI's autonomy: 0.2107
- B2 autonomy: 0.992
- LEXI / B2 wrong-commit ratio: 0.10 [0.05, 0.17]
- B2 autonomy at LEXI's risk: 0.0
- B3 wrong-commit rate at LEXI's autonomy: 0.0178
- B3 autonomy: 0.481
- LEXI / B3 wrong-commit ratio: 1.18 [0.64, 2.27]
- B3 autonomy at LEXI's risk: 0.481

## ablations

- A1: no ledger prior: wrong rate +0.0015 [-0.0081, +0.0113] at autonomy 0.418
- A2: top-1 transcript, no lattice: wrong rate +0.0008 [-0.0086, +0.0101] at autonomy 0.430
- A7: no channel features: wrong rate +0.0024 [-0.0069, +0.0101] at autonomy 0.438
- A6: what=no hard limits max_loss_with_limits=17670 max_loss_without=17670
- A3: what=yes/no probes only: needs a caller model run with the probe planner restricted status=not run
- A4: what=no sentinel: needs a shift run with the sentinel's effect on the registry switched off status=not run
- A5: what=Evon emits values: needs the live parser status=not run
- A8: what=fixed endpointing: needs a run with gateway.numeric_hold off status=not run

## per_cell

- hi.Q1.T1: 130 of 184 committed, 1 wrong, Clopper-Pearson [0.000, 0.042] against alpha 0.05: holds
- hi.Q1.T2: 106 of 129 committed, 0 wrong, Clopper-Pearson [0.000, 0.034] against alpha 0.1: holds
- hi.Q2.T1: 0 of 107 committed, 0 wrong, Clopper-Pearson [0.000, 1.000] against alpha 0.05: no certificate
- hi.Q2.T2: 0 of 78 committed, 0 wrong, Clopper-Pearson [0.000, 1.000] against alpha 0.1: no certificate
- hi.Q3.T1: 0 of 47 committed, 0 wrong, Clopper-Pearson [0.000, 1.000] against alpha 0.05: no certificate
- hi.Q3.T2: 0 of 42 committed, 0 wrong, Clopper-Pearson [0.000, 1.000] against alpha 0.1: no certificate
- ta.Q1.T1: 153 of 209 committed, 6 wrong, Clopper-Pearson [0.015, 0.083] against alpha 0.05: holds
- ta.Q1.T2: 83 of 105 committed, 1 wrong, Clopper-Pearson [0.000, 0.065] against alpha 0.1: holds
- ta.Q2.T1: 0 of 124 committed, 0 wrong, Clopper-Pearson [0.000, 1.000] against alpha 0.05: no certificate
- ta.Q2.T2: 50 of 88 committed, 3 wrong, Clopper-Pearson [0.013, 0.165] against alpha 0.1: holds
- ta.Q3.T1: 0 of 27 committed, 0 wrong, Clopper-Pearson [0.000, 1.000] against alpha 0.05: no certificate
- ta.Q3.T2: 0 of 27 committed, 0 wrong, Clopper-Pearson [0.000, 1.000] against alpha 0.1: no certificate

## risk_coverage

- hi.Q1.T1: [[0.0051, 0.5435, 0.01], [0.0236, 0.6848, 0.0079], [0.0323, 0.6902, 0.0079], [0.114, 0.7989, 0.034], [0.2482, 0.8967, 0.0606], [0.3679, 0.9402, 0.1156], [1.0, 0.9511, 0.1429]]
- hi.Q1.T2: [[0.0051, 0.7132, 0.0], [0.0236, 0.7984, 0.0], [0.0245, 0.7984, 0.0], [0.114, 0.8992, 0.0086], [0.2482, 0.9535, 0.0325], [0.8571, 0.9845, 0.0551]]
- hi.Q2.T1: [[0.0051, 0.271, 0.0], [0.0236, 0.4112, 0.0], [0.0988, 0.5888, 0.0476], [0.114, 0.7477, 0.075], [0.2482, 0.8598, 0.1304], [0.3182, 0.9159, 0.1531], [0.3679, 0.9533, 0.1765], [1.0, 0.9533, 0.2059]]
- hi.Q2.T2: [[0.0051, 0.6923, 0.037], [0.0236, 0.7436, 0.069], [0.0892, 0.7564, 0.0678], [0.114, 0.8462, 0.0606], [0.2813, 0.8974, 0.0714], [0.8421, 0.9615, 0.0933]]
- hi.Q3.T1: [[0.0837, 0.1064, 0.2], [0.1569, 0.2553, 0.1667], [0.2482, 0.4043, 0.1053], [0.3679, 0.6383, 0.3], [0.3682, 0.6383, 0.3], [0.6842, 0.7447, 0.3429], [0.8571, 0.8085, 0.3947]]
- hi.Q3.T2: [[0.0051, 0.2619, 0.0], [0.0236, 0.4762, 0.0], [0.114, 0.5952, 0.04], [0.1569, 0.6667, 0.0357], [0.2742, 0.7619, 0.0312], [0.3679, 0.8571, 0.1111], [1.0, 0.9048, 0.1842]]
- ta.Q1.T1: [[0.0051, 0.3397, 0.0141], [0.0236, 0.5646, 0.0254], [0.0909, 0.6555, 0.0219], [0.114, 0.7943, 0.0542], [0.1569, 0.8373, 0.0743], [0.3182, 0.9043, 0.1005], [0.3679, 0.9426, 0.1777], [1.0, 0.9761, 0.2255]]
- ta.Q1.T2: [[0.0051, 0.6571, 0.0], [0.0236, 0.7714, 0.0], [0.0909, 0.7905, 0.012], [0.1569, 0.8857, 0.043], [0.2482, 0.9714, 0.0588], [1.0, 1.0, 0.0952]]
- ta.Q2.T1: [[0.0051, 0.2097, 0.0], [0.0236, 0.3629, 0.0], [0.0988, 0.621, 0.026], [0.1375, 0.7097, 0.0341], [0.2482, 0.8226, 0.0784], [0.3182, 0.8468, 0.1238], [0.3679, 0.8952, 0.1982], [1.0, 0.9194, 0.2632]]
- ta.Q2.T2: [[0.0051, 0.5682, 0.06], [0.0236, 0.6818, 0.05], [0.0988, 0.7727, 0.0588], [0.114, 0.8295, 0.0548], [0.3182, 0.9091, 0.0875], [1.0, 0.9545, 0.1429]]
- ta.Q3.T1: [[0.114, 0.2222, 0.0], [0.3162, 0.4074, 0.0], [0.3679, 0.7037, 0.2105], [0.3684, 0.7778, 0.1905], [0.7983, 0.7778, 0.2381], [0.8571, 0.8148, 0.4091], [1.0, 0.8519, 0.4348]]
- ta.Q3.T2: [[0.0051, 0.3333, 0.0], [0.0178, 0.3333, 0.0], [0.0658, 0.5185, 0.0], [0.114, 0.7407, 0.05], [0.16, 0.7407, 0.05], [0.3182, 0.7778, 0.1429], [0.5, 0.8148, 0.1818], [1.0, 0.8519, 0.1739]]

## checks

- decisions replayed through decide(): 1000 of 17128 in the audit
- audit records: 32747
- failing audit tests: 0
- failing ledger tests: 0
- live c3_p50_ms: 0.776
- live c3_p99_ms: 2.877
- live c4_p50_ms: 0.047
- live c4_p99_ms: 0.266
- live c5_p50_ms: 0.563
- live c5_p99_ms: 1.016
- live decide_p50_ms: 0.061
- live decide_p99_ms: 0.126
- live audit_ack_p50_ms: 10.409
- live audit_ack_p99_ms: 328.494
- live core_p50_ms: 0.671
- live core_p99_ms: 1.281
