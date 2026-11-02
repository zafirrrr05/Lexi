# Runbook

How to start, watch and recover LEXI. Commands assume the venv is active (`source .venv/bin/activate`) and you are in the project folder.

## Processes

| process | command | what it is |
|---|---|---|
| server | `lexi serve` | gateway, evidence, scorer, gate, probes, ledger, sentinel, authority, audit and the console on port 8000 |
| twin | `lexi twin` | the synthetic callers; a separate OS process with a control port on 8001; it reaches the server over WebSocket |

The server alone is a complete system: a real telephone bridge could replace the twin. `--speed S` (same value on both) runs call time S times faster, including the undo window,
the lease and the heartbeat. Use it for demos and bulk collection, never for a soak.

Stop with Ctrl-C. The audit log flushes its group commit, the ledger closes cleanly and the next start verifies the chain tail.

## Data that matters

Everything lives in `data/`.

| file | holds | if lost |
|---|---|---|
| `certs.db` | certificates, registry versions, lease heartbeat | there is no registry, so every call escalates (rule R0) until `lexi certify` has run |
| `artifacts/` | the fitted scorer and its isotonic map, hash-checked at load | same as above |
| `labels.db` | labeled calls (Train, Valid, Calib, Test), stored decisions, custody log | certificates cannot be re-issued or audited; Test can never be regenerated under the same tag |
| `ledger.db` | event-sourced ledger | the ledger history |
| `audit/` | hash-chained decision log, segmented | the evidence trail; verify it with `lexi audit-verify` |
| `providers_cache/` | recorded provider responses | nothing important: it is rebuilt by calling the providers |

Back up `certs.db`, `labels.db`, `ledger.db` and `audit/` together.

## Start-up order and what to expect

1. `lexi serve` loads the registry and scorer. With no registry the gate has nothing to certify against and every call escalates (rule R0).
2. It recovers the ledger: entries prepared but never delivered are compensated, delivered entries are posted once their undo window has passed, and anything missing from the audit
   is re-appended with a `recovered` flag.
3. Autonomy stays off until the sentinel's first heartbeat has renewed the lease (a few seconds). The lease ring in the console header shows it.
4. `lexi twin` waits for the server and then idles until a scenario is started on the console (Scenario, Start stream).

## Reading the console

- **Header**: mode, scorer version, audit chain head hash, audit and ledger health, lease countdown. A red audit or ledger pill means autonomy is off (see below).
- **Certificates**: green = HEALTHY and certified, amber = WATCH (hedged threshold, no guarantee), red = ALERT or REVOKED. A cell with no certificate shows why.
- **Scenario / Recertification**: after a cell leaves HEALTHY the twin collects fresh labeled calls for it; the bar fills toward n_target and a new certificate is issued when it is full.

## Incidents

| what you see | what it means | what to do |
|---|---|---|
| Audit pill red | the audit write failed or its fsync is too slow; every commit is converted to an escalation | check the disk that holds `data/audit`; autonomy returns on its own when writes succeed again; run `lexi audit-verify` afterwards |
| Ledger pill red | a ledger write failed; autonomy is off for 30 s and no success is spoken | check `data/ledger.db`; the entry was rejected, nothing is half posted |
| Lease ring empty, all cells escalate | the sentinel stopped or the registry is older than its maximum age; autonomy is off everywhere | restart the server; the first heartbeat restores autonomy |
| One cell WATCH | the detectors saw a possible shift or the label supply has stopped; the threshold is hedged | keep labeling; it clears when the evidence settles |
| One cell ALERT, then REVOKED | the shift is confirmed; new calls in that cell go to the queue | resolve tickets on the **Queue** screen; recertification runs by itself |
| Recertification does not start | the twin is not running, or its `--speed` differs from the server's | start `lexi twin` with the same speed |
| A cell never gets its certificate back | the fresh Calib window failed the stationarity pre-check (about 1 in 7 windows do, see D-009) or no stricter threshold passes | it collects again on its own; if it persists the cell is too noisy for autonomy and stays on the queue, which is the safe outcome |
| Process killed with `kill -9` | any state | start it again; recovery needs no manual step; autonomy stays off until the first heartbeat |

## Checks

```
lexi audit-verify          # walk the whole hash chain; prints `{ok, n_records, first_bad_seq}` and exits non-zero if the chain is broken
lexi replay --sample 1000  # replay stored decisions through the pure decision core; any mismatch is a bug
lexi bench --tag <tag>     # the release report; a release is verified only if every MUST row passes
make topology              # the code only connects what the architecture allows
```

## Releasing a new scorer or thresholds

1. Change the code or the configuration. Anything that alters a guarantee goes through the specification first and is noted in `docs/DECISIONS.md`.
2. `lexi certify` re-collects what is missing, fits, issues certificates and bumps the registry version. The server pairs scorer and registry versions at load and refuses a mismatch.
3. `lexi bench --tag <new tag>`. The Test split is opened once per tag by the custody layer; a second request for the same tag is refused.
4. Read `results/<tag>/report.md`. Rows marked `not measured` are unmeasured, not passed.

## Going live with the hosted providers

1. Put the keys in `.env` (`GNANI_API_KEY`, `EVON_API_KEY`).
2. Run `python scripts/phase0_probe.py` to measure what the hosted services return; it rewrites `docs/PHASE0.md`.
3. Set `providers.mode: live`. Rehearse with `providers.cache: read_through`; never present cached output as live.
4. Check word-level confidence. If the recogniser does not return it, every turn is marked uncertifiable and escalates (D-015) until a confidence proxy is added.
5. Certificates issued on simulated calls do not carry over: re-run `lexi certify` against live calls.
