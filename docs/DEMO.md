# The one-minute demo

What the viewer should take away in a minute: a wrong action is stopped by a calibrated bound, the bound is visible, and it withdraws itself when the world changes.
Everything on screen is computed from the live audit stream and the gold labels; nothing is typed in.

## Before you start

Two terminals, the same `--speed` on both so call time, undo window and lease run 20 times faster:

```
.venv/bin/lexi serve --speed 20
.venv/bin/lexi twin --speed 20
```

Open http://127.0.0.1:8000 in a window of about 1440 x 900. The status dot at the top right reads `live`; the lease ring is full.

1. **Scenario**, rate 60 calls / min, **Start stream** on *Reference traffic*. Let it run for about 30 seconds so the hook and the counters have something to show.
2. **Risk**, **Reset counters**, so the end card counts only the take.
3. Keys `1` to `7` switch screens, `Esc` closes the end card.

Keep the rate between 40 and 80 calls / min and leave the twin at its default concurrency of 4: both processes share one machine, and above that the server answers late
(D-020).

## Run sheet

The times are those of the edited one-minute cut. The raw take is two to three minutes, because the detectors need traffic to cross their thresholds (see *What was measured*).

| time | screen | what happens | what to say |
|---|---|---|---|
| 0:00 | **Live call** | LEXI on the left, the naive baseline on the right, same call. The headline is the latest call where the baseline would have posted a wrong value and LEXI did not, with both values side by side. | A recogniser under 4% word error still moves the wrong rupee amount sometimes. The baseline posts what it heard; LEXI asked, or sent it to a person. |
| 0:10 | **Certificates** | One card per language, channel quality and tier. Click a card: the bound, the number of accepted calls, errors, p-value and the threshold, for each rung. | Each green card is an exact binomial bound on labeled calls: wrong-action rate at most 5% (or 10% for the lower tier) with probability at least 97.5%. The grey cards have no certificate, so every call there goes to a person. |
| 0:20 | **Scenario** | Click **New parties** while the stream runs. The detector traces on the Certificates screen start to climb on a log scale. | The callers now name people the ledger has not seen. The certificate was earned on different traffic. |
| 0:35 to 0:50 | **Certificates** | The cell turns amber (WATCH), then red (ALERT, REVOKED) once an e-value crosses 10^3, 10^4, 10^5. The toast names the detector and its value. | The detector is an e-process on the score stream, so it can be watched continuously without inflating false alarms. The certificate withdraws itself. |
| 0:50 | **Queue** | Calls of the revoked cell now arrive as tickets. Open one, pick the party and amount, **Resolve**. | New calls in that cell go to a person; one click posts the entry with the human's values. |
| 0:55 | **Scenario** | The *Recertification* box shows the cell and a bar that fills with labeled post-shift calls toward n_target. | A new certificate is issued when the bar is full, with a threshold fitted to the new traffic. |
| 1:00 | **End card** | Wrong commits, LEXI against the baseline; autonomy rate; wrong commits before containment; CELB-cap and CELB-est in rupees; the audit chain head. | Every number comes from the audit log and the labels. The chain head lets anyone verify the log. |

## What was measured

Simulated providers, `--speed 20`, a two-core virtual machine, stream at 60 calls / min requested (about 75 completed a minute with the shipped data), one run of eight
minutes:

| from the injection of *New parties* | seen |
|---|---|
| 35 s | first certified cell goes WATCH |
| 1 to 3 minutes | the first cell is REVOKED (e-value above 10^5); the time depends on which calls arrive, the runs gave 56, 65 and 170 s |
| about 400 s | both affected cells hold a new certificate and are HEALTHY again |

At the end of that run the end card read: LEXI 6 wrong commits of 272 autonomous (2.2%), the baseline 17 of 230 (7.4%), autonomy 39.8% against 33.6%, 3 wrong
commits between the onset and the first REVOKED cell.

The last row is bound by how fast the twin can label calls (a cell needs about 200 post-shift calls, because only part of them is accepted at the stricter
threshold); a faster machine shortens it. Plan the recording as a take of two to three minutes that is cut after the REVOKED beat, and let the stream run in the
background if you want to show the certificate coming back at the end.

## Why *New parties* and not the monsoon

Section 15 of the specification injects the monsoon (S1). In this build S1 moves the calls to the Q3 channel-quality cells, which hold no certificate (D-016), so
autonomy drops to zero and nothing wrong is posted, but there is no certified cell for the detectors to revoke. It is still worth one beat if you want to show the
autonomy dip: inject *Monsoon*, watch the autonomy rate on **Risk** fall, then inject *New parties* for the detection beat. *New parties* lands on certified cells and
runs the whole sequence. The reasoning is in D-019.

## What to expect honestly

- Five of the twelve cells hold a certificate; the other seven always escalate. That is the result of the simulated recogniser's silent-error floor (D-016), not a
  display choice, and the board says so on each grey card.
- Between the injection and the REVOKED beat a certified cell can still commit a wrong value; the end card shows that count. A certificate is a bound on
  traffic like the one it was earned on, and the detectors exist for the moment that stops being true.
- A certified cell can enter WATCH on ordinary traffic with no shift injected. WATCH only hedges the threshold, and it clears by itself once the evidence settles.
- After a recertification the new bound is for the new traffic; the older certificate is not restored.

## After the take

- `lexi audit-verify` prints `{"ok": true, ...}` for the whole chain.
- `lexi bench --tag <tag>` writes the report that the **Eval** screen shows; any figure quoted in a post should be one that exists in `results/<tag>/report.json`.
- Say plainly that the providers were simulated, as the header of the console does.
