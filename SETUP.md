# Setup

Everything runs on one machine. No account or API key is needed for the default (simulated) providers.

## You need

- Linux or macOS (Windows through WSL)
- Python 3.11 or newer
- `make` (optional, every `make` target is one command you can type yourself)
- A modern browser (Chrome, Edge, Firefox, Safari)
- About 4 GB of free disk and 4 GB of memory

## 1. Install

```
cd Lexi
make venv
```

This creates `.venv/`, installs `requirements.txt` and installs the project in editable mode, which also gives you the `lexi` command inside the venv.
Without `make`:

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -e .
```

Activate the environment if you want to type `lexi ...` directly: `source .venv/bin/activate`.

## 2. Keys (optional)

```
cp .env.example .env
```

Leave the keys empty to stay on the simulators. To use the hosted services, fill in `GNANI_API_KEY` (speech to text and text to speech) and `EVON_API_KEY` (parsing and
paraphrase) and set `providers.mode: live` in `config/runtime.yaml`. Keys are only ever read from the environment; `.env` is git-ignored.

## 3. Certificates

LEXI posts autonomously only under a certificate, and certificates are issued from labeled calls. Two cases:

- **You received `Lexi-code.zip` and `Lexi-data.zip.001`, `.002`, `.003`.** The data parts hold the labeled calls and the audit chain that belong to the issued certificates. Put all four files in one empty folder named `Lexi` and unpack them there. The archives have no top-level folder, so they unpack straight into the folder you are in:

  ```
  cd Lexi
  unzip Lexi-code.zip
  cat Lexi-data.zip.00* > Lexi-data.zip
  unzip Lexi-data.zip
  ```

  Windows (command prompt, no extra tools):

  ```
  cd Lexi
  tar -xf Lexi-code.zip
  copy /b Lexi-data.zip.001+Lexi-data.zip.002+Lexi-data.zip.003 Lexi-data.zip
  tar -xf Lexi-data.zip
  ```

  `data/` then holds `audit/` and `labels.db` beside the scorer and certificates. Do not start the server on a `data/` that has only half of this: the audit chain and `labels.db` are one record. Go to step 4.
- **There is no `data/` folder, or only some of the parts** (for example a fresh clone). Delete `data/` and build it once:

  ```
  make bootstrap
  ```

  This collects about 7,000 synthetic calls through the caller twin, fits the scorer and issues the certificates. It is CPU bound and takes one to two hours on a
  laptop. It is safe to interrupt: run it again and it carries on from the counts already stored. Until certificates exist every call goes to the human queue.

## 4. Run

Two terminals, both in the project folder.

Terminal 1, the server and the console:

```
make serve
```

Terminal 2, the caller twin (it phones the server the way a telephone bridge would):

```
make twin
```

Open http://127.0.0.1:8000. The status line at the top right should read `live`.

To see the whole loop in about a minute, run both processes with compressed call time, with the same factor on both:

```
.venv/bin/lexi serve --speed 20
.venv/bin/lexi twin --speed 20
```

Then follow [docs/DEMO.md](docs/DEMO.md).

## 5. Check the build

```
make test         # unit, property, statistical, end-to-end and fault tests
make lint         # ruff
make typecheck    # mypy on the decision-critical packages
make topology     # the code connects only the components the architecture allows
```

## 6. Evaluation report

```
.venv/bin/lexi bench --tag v2
```

Replays the sealed test split at the certified thresholds and writes `results/<tag>/report.md` and `report.json`, which the **Eval** screen of the console then shows. The test split
is served once per tag: the archive already holds `v1`, so use a new tag (`v2`). The latency rows are measured from live decisions in the audit, so run the demo stream for a few
minutes first and the report will describe your machine.

## Troubleshooting

| symptom | cause and fix |
|---|---|
| The page loads but the caller twin shows as offline | `make twin` is not running, or it uses a different `--speed` than the server |
| Every call is escalated and the certificate board is empty | no certificates yet: run `make bootstrap`, then restart `make serve` |
| `Address already in use` | another process holds port 8000 or 8001: stop it, or change `server.port` / `twin.control_port` in `config/runtime.yaml` |
| `webrtcvad` fails to install | use the Python version of your OS package manager, or upgrade pip; the wheels in `requirements.txt` cover Python 3.8 to 3.13 |
| Softphone says it needs live providers | the simulated recogniser listens to the twin's script, not to audio; switch to `providers.mode: live` and add keys |
| Calls pile up or the stream stalls | the stream is faster than the machine can serve: stop it and start again at 40 to 80 calls / min with the twin at its default concurrency |
| A run was interrupted halfway | start it again; collection resumes, and the audit log verifies with `lexi audit-verify` |

More on operating the system is in [docs/RUNBOOK.md](docs/RUNBOOK.md).
