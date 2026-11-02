# LEXI

**Ledger Execution X-risk Intercept**: a control layer for voice agents that post to a ledger.

A caller says "Sharma ji ka paintees hazaar aaya". Was it 35,000 or 45,000? A speech recogniser at 4% word error rate cannot tell you whether *this* amount is right,
and a wrong rupee posted to a ledger is hard to take back. LEXI sits between the voice stack and the ledger and answers one question before anything is posted:
**is the chance that this action is wrong small enough to act on without a person?**

## What it does

- **Reads the amount from the audio, never from the language model.** The transcript is turned into typed candidates (amounts, dates, parties) by deterministic code.
  The language model may only pick one of those candidates, so a made-up amount cannot reach the ledger.
- **Scores every action** with a calibrated probability that a critical field (intent, party, amount, date) is wrong, using the candidates, how confusable the
  numbers sound, what the ledger already knows about this party, and the quality of the phone line.
- **Posts on its own only inside a certificate.** For each language, line-quality and amount tier there is a threshold backed by an exact binomial test on labeled
  calls: among actions posted autonomously, the wrong-action rate is at most alpha, with a stated confidence.
- **Asks the cheapest question that settles it.** When the score is too high it asks the caller one targeted question ("Pehla ya doosra? paintees hazaar,
  paintalis hazaar"), and only then hands over to a human queue.
- **Watches itself.** Statistical detectors compare the live calls with the data the certificates were issued on. When the world stops matching (monsoon noise, a
  codec change), cells go WATCH, then ALERT, then REVOKED, and new calls go to people until fresh labeled calls earn a new certificate.
- **Leaves a receipt.** Every decision is written to a hash-chained audit log before it is acted on, and the ledger is event-sourced with an undo window.

## The console

`make serve` starts the system and a console in the browser at http://127.0.0.1:8000.

1. **Live call**: LEXI on the left, a naive baseline (commit when the lowest word confidence is high enough) on the right, on the same calls, with its own ghost ledger.
2. **Certificates**: one card per cell with the bound, the number of accepted calls, errors, p-value and threshold.
3. **Risk**: wrong-commit counts, autonomy rate and the rupee exposure, LEXI against the baseline.
4. **Queue**: what the system declined to commit, resolved by a person in one click.
5. **Scenario**: start a stream of synthetic callers and inject a shift (for example the monsoon scenario), then watch the detectors react and the certificate come back.
6. **Eval**: the latest report written by `lexi bench`.
7. **Softphone**: talk to the agent from the browser (needs live speech providers).

## Run it

See [SETUP.md](SETUP.md). The short version:

```
make venv
make serve        # terminal 1
make twin         # terminal 2
```

Open http://127.0.0.1:8000, go to **Scenario**, press **Start stream**, then inject a shift.

## Simulated providers

The speech recogniser (Prisma), the speech synthesiser (Timbre) and the language model (Evon) each have a simulator that runs offline and is reproducible, and a live
adapter that talks to the hosted service. The default is the simulator, so every number in this repository was measured on simulated providers, and the console says
so in its header. To use the hosted services put the keys in `.env` and set `providers.mode: live` in `config/runtime.yaml`. The choices behind this, and every place where the
build clarifies the design, are in [docs/DECISIONS.md](docs/DECISIONS.md); [docs/PHASE0.md](docs/PHASE0.md) records what was and was not measured against the real endpoints.

## Commands

| command | what it does |
|---|---|
| `lexi serve` | the server, the gate, the ledger and the console |
| `lexi twin` | the synthetic caller twin, a separate process that phones the server |
| `lexi certify` | collects labeled calls, fits the scorer and issues certificates |
| `lexi bench --tag T` | replays the sealed test split and writes `results/T/report.md` and `report.json` |
| `lexi replay` | replays stored decisions through the pure decision core and checks they match |
| `lexi audit-verify` | verifies the audit hash chain |

`make test` runs the test suite, `make lint` and `make typecheck` run ruff and mypy, `make topology` checks that the code only connects the way the architecture allows.

More: [SETUP.md](SETUP.md) to run it, [docs/RUNBOOK.md](docs/RUNBOOK.md) to operate it, [docs/DEMO.md](docs/DEMO.md) for the one-minute walkthrough.
