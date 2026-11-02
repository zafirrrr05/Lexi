# Decisions

One entry per place where the build adds to, clarifies or departs from `docs/LEXI_SPEC.txt` (what, why, sections). The spec stays the source of truth;
nothing here changes a guarantee.

## D-001 Provider simulators are the default

**What.** Prisma, Timbre and Evon each have a `sim` mode next to the `live` adapter. `config/runtime.yaml` ships `providers.mode: sim`. The simulators are
deterministic given a seed: a formant synthesiser for Timbre, a per-token noisy transcript for Prisma (substitution probability rises as SNR falls and with
packet loss, numerals, scales and names are hit 1.5 times harder, confusable groups attract the substitution, confidences are informative but overlap), and an
answer from the lexicons for Evon.
**Why.** The build had no provider account, and the demo and the evaluation have to run offline and be reproducible. The live adapters follow the published
endpoint documentation and have not been exercised against a real account (V1-V5 are unmeasured, see `PHASE0.md`).
**Consequence.** Every number in `results/` and on the console was produced on simulated providers. Rehearsing on a cache is allowed by section 15; presenting
simulated output as live is not.
**Sections.** 13, 15, 17.

## D-002 Additive fields and the wire protocol

**What.** Fields marked `(ext)` in `common/types.py` are additions to 8.2: `TurnInput.long_utterance` and `.collect`, `ProbeSpec.options`,
`ScoredProposal.intent / intent_agreement / dup_cleared / ctx`, `ProbeOutcome.spec`, `EscalationTicket.session_id`, `Expects.facts`, `CallControl.text` and
`.duration_ms`, `Receipt.session_id / lang`, `GoldLabel.spoken`, `LabeledOutcome.autonomous`, `ScenarioParams.n_calls / split / cell`,
`TtsAudio.word_timings`, `LedgerView.today / clock_ts`, `Profile.trailing_silence_ms`.
On WebSocket `/call` binary messages are 320-byte s16le frames (8 kHz, 20 ms); text messages are JSON. C1 sends `CallControl` (the first one, with empty text,
tells the caller it may speak). The caller sends `announce {utt_id, clip_start_ms}` in simulation mode only, so the simulated recogniser knows which clip starts
when. TTS audio is sent only to the browser softphone: the Twin takes the length of what the system said from `CallControl.duration_ms`.
**Why.** Each field is needed by a step the spec describes but whose message it left too thin. Nothing is removed from 8.2.
**Sections.** 7 (C1, C3, C7, C8, C9, C11), 8.2.

## D-003 Evon live endpoint

**What.** The live Evon adapter speaks an OpenAI-compatible chat-completions interface at `providers.evon.url`, key in `EVON_API_KEY`. It never raises: any
failure is a status the parser turns into a deterministic fallback.
**Why.** The hosted interface was not available to check. A different shape changes one adapter and no contract.
**Sections.** 13, 17.

## D-004 Prompt phrases need a native review

**What.** The Hindi and Tamil system phrases in `config/prompts/{hi,ta}.yaml` are hand written. A native speaker has to review them before any real deployment.
**Sections.** 7 (C8), 13.

## D-005 An unsigned intent is confirmed, not contrasted

**What.** When the intent is not signed by a strong cue, C8 asks a yes/no read-back instead of an intent contrast. 4.3 offers a contrast only between the two
signed readings (credit against debit), so there is no second reading to contrast an unsigned intent with.
**Sections.** 4.3, 7 (C8).

## D-006 Scorer and detector details the spec leaves open

**What.** Slot temperatures stay at 1.0: fitting them moves the features, and the isotonic map already calibrates. M2 only bets with the grid values of nu for
which nu x alpha_0 < 1, because a larger bet could lose more than its stake (at alpha_0 = 0.10, nu = 16). The canary rate is 6 per minute (the spec leaves it open;
about one labeled commit per cell every ten seconds while a cell is WATCH or worse). The sentinel checkpoints every 100 events and on every transition, so a
checkpoint cannot bring an ALERT back as HEALTHY. The CERT-v1 grid starts below alpha_r when Valid already shows n_target commits there.
**Sections.** 10.3, 10.5, C5, C10.

## D-007 End of utterance on a noisy line

**What.** webrtcvad never reports silence on a noisy line. The endpointer therefore falls back to the stream's own quiet plus 650 ms.
**Sections.** C1.

## D-008 The demo hook

**What.** Section 15 uses 35,000 rupees, but `max_autonomous_amount_inr` is 25,000, so a call of that size is escalated by a hard limit before any certificate
is consulted. The console does not script a call. Its hook is computed: the latest finished call where the naive baseline would have posted a wrong value and
LEXI did not, with both values side by side. Amounts of 25,000 or less are what the certificates govern.
**Sections.** 4.5, 15.

## D-009 The stationarity pre-check refuses some good windows

**What.** C6 refuses a Calib window whose SRM1 passes 1000 when read in time order. Simulating i.i.d. windows of the sizes used here, about 12 to 17 percent
are refused although nothing shifted. A refused cell has no certificate until its Calib window is collected again; it is never certified on a window that failed.
**Sections.** C6 step 4, 10.5.

## D-010 Calls without a cell

**What.** A call whose stage-0 turn yields no intent has no cell (the cell needs the intent's tier). It is excluded from calibration records and per-cell numbers
and always escalates.
**Sections.** 4.4, C6, C14.

## D-011 What the scorer learns from, and what its ECE means

**What.** The scorer is trained on the features recorded at collection time (`scorer` rows of the label store). The isotonic map is fitted on Valid, so the
Valid ECE (RC2) is in-sample by construction; the report also gives the ECE on Test at stage 0.
**Sections.** C5 step 4, 11 (RC2).

## D-012 Collection inside a live server, and recertification

**What.** A call opened with `/call?collect=true` runs in COLLECT mode (`TurnInput.collect`) whatever the process mode is. When the sentinel takes a cell out of
HEALTHY it raises a canary request, the Twin collects post-onset Calib calls for that cell under the scenario now playing, and when the deficit of the cell
(K15) reaches zero the server issues certificates for it again; the sentinel's references are reset and the cell clears. The live traffic of other cells is
not touched.
**Why.** D12 describes recertification but a LIVE process cannot collect; a per-session flag avoids a second server.
**Sections.** D12, C6, C7, C10, C11.

## D-013 The console sees labels through the composition root

**What.** Screen 3 needs gold-labeled outcomes (wrong-commit rate, ghost balances) and the baseline shadow, and the edge list has no gold-to-C13 edge. The
composition root hands the console the finished-call summaries and outcomes as a display stream; the console's `Tally` is a fold over the audit, call and outcome
streams and owns nothing authoritative. The label store still holds the only copy of gold, and Test never reaches the console.
**Sections.** C13, 8.1, P5.

## D-014 The idle phone line carries noise

**What.** A Twin that sends digital silence between utterances makes C2 read the zeros as packet loss and an infinite SNR, and every call is profiled Q3. The
Twin's idle line carries the call's own channel noise; speech is normalised to a fixed level and the noise is set relative to it.
**Sections.** C2, C11.

## D-015 Unverified live provider details

**What.** Published Prisma samples show no word-level confidence on the endpoint used here (V5). In live mode words arrive with `conf = None`, C3 raises
`conf_missing`, and every turn is uncertifiable until a confidence proxy is added (the cut-list item of section 18, a change through section 19). Timbre accepts
speaking rates between 0.85 and 1.15 and has no pitch control, so pitch variants exist in simulation only.
**Sections.** 17 (V4, V5), 18, 19.

## D-016 The simulated recogniser's silent-error floor

**What.** The first simulator let about 3 percent of the calls in the lowest-risk bin go wrong without any signal (a dropped numeral, a flipped cue, a
confident substitution). C6 correctly refused to certify most cells: at alpha = 5 percent a cell with 4 errors in 145 accepted calls fails the exact test, and
the fixed-sequence procedure stops at the first failing grid point. The simulator was made milder in three places and every split was collected again:
substitution probability 2 percent at 18 dB or better, rising to 8 percent at 8 dB and 25 percent at the worst (was 3, 12 and 35 percent); packet loss counts once,
not twice; deletions are 1 percent of the substitution probability (was 15, then 5); cue flips 0.2, 1.2 and 4 percent by quality bucket; confidences of wrong words
at low SNR are a little lower. This is a parameter of the simulated provider, not a change to the method: the certificate test and the scorer are untouched, and
a real recogniser's floor decides how many cells certify.
**Sections.** 10.3, D-001.

## D-017 What the report measures, and what it does not

**What.** `lexi bench` replays the sealed Test trajectories (E1) at the certified lambdas and reports autonomy, wrong-commit rate with Clopper-Pearson intervals,
certificate validity per cell, interception, CELB, B1 to B3, and ablations A1, A2, A6 and A7. B2 uses the stage-0 intent posterior as the parser's confidence
(Evon's own number is not recorded). Rupee figures use the gold amount as the exposure of a wrong commit. RC7 replays 1,000 stored decisions through
`decide()`; RC6 is `tests/stats/test_no_hallucination.py` (100,000 random bundles against an adversarial Evon); RC8 and RC9 are the audit-chain check plus the audit and ledger fault tests. Ablations A3, A4, A5, A8 and the S6 null experiment need runs this
build did not make, and their rows read `not measured` in the report; so do the SLO rows nothing measured, and a release with an unmeasured MUST row is not
LEXI-verified. The latency rows (L1 to L3) come from the live decisions in the audit, so they describe the machine that produced them: the shipped v1 report was
measured on two shared cores where one fsync takes about 10 ms, and L3 (audit ack p99, target 5 ms, which assumes a local SSD) fails there with 328 ms. Run a live
stream and `lexi bench` with a new tag on the target machine to get its own figures. `scripts/validate_math.py` reproduces the figures of 10.5.
**Sections.** 11, 16.

## D-018 Compressed call time

**What.** `--speed S` runs call time S times faster than the wall clock (undo window, lease, heartbeat, call length), on the server and on the Twin
alike. It is how a section 15 sequence fits in a minute and how a data run of thousands of calls finishes in an hour.
**Sections.** 15, C11.

## D-019 The shift the demo injects

**What.** Section 15 injects S1, the monsoon. In this build S1 sends nearly every call to the Q3 channel-quality cells, and none of them holds a certificate (D-016),
so S1 shows the autonomy dip and the ghost ledger diverging, but there is no certified cell for the detectors to revoke. S4, new parties outside the registry,
lands on certified cells (hi.Q1.T1, hi.Q1.T2 and the Tamil Q1 cells), so it is the shift that runs the whole sequence: WATCH, ALERT, REVOKED, recertification.
The console keeps all seven scenarios and marks S4 as the one the certified cells react to; `docs/DEMO.md` names the order. If a recogniser's real error floor lets
Q3 cells certify, S1 will take that role without any code change.
**Sections.** 15, C11.

## D-020 Load, and WebSocket keep-alive

**What.** The server runs gateway, scorer, gate, detectors and audit on one event loop. On a two-core machine it sustains 40 to 80 calls a minute with the Twin at
concurrency 4 and `--speed 20`; above that the loop answers late, and WebSocket ping timeouts used to drop healthy calls. Keep-alive pings are therefore off on the
gateway and the Twin (`ws_ping_interval=None`, `ping_interval=None`); a dead peer is still handled by the call's own end-of-stream and timeout rules. The Twin's HTTP
clients also open one connection per request, because a keep-alive connection reused while the server loop was busy raised `RemoteProtocolError`.
**Sections.** 13, C1, C11.
