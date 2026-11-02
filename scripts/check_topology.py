"""Holds the single edge list that spec 8.1, 8.4 and 8.5 are generated from, and enforces TP1-TP8.

    python scripts/check_topology.py           # exit 1 on any violation
    python scripts/check_topology.py --print   # print the generated 8.1 / 8.4 / 8.5 blocks

Checks: TP1-TP8 on the edge list; the 38 message types in common/types.py equal the edge messages;
package imports under src/lexi follow the ports of section 8 (TP5 included); and, when
docs/LEXI_SPEC.txt is present, that its 8.1, 8.4 and 8.5 equal what this script generates.
"""

import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "LEXI_SPEC.txt"
SRC = ROOT / "src" / "lexi"

COMPONENTS = {
    "C1": "Voice Session Gateway", "C2": "Channel Profiler", "C3": "Evidence Builder", "C4": "Action Parser",
    "C5": "Risk Engine", "C6": "Certificate Authority", "C7": "Autonomy Gate", "C8": "Probe & Speech Planner",
    "C9": "Action Ledger", "C10": "Drift Sentinel", "C11": "Synthetic Caller Twin", "C12": "Decision Audit Log",
    "C13": "Operator Console", "C14": "Label & Calibration Store", "C15": "Evaluation Harness",
}
PROVIDERS = {"PRISMA", "EVON", "TIMBRE"}
PACKAGE = {"C1": "gateway", "C2": "profiler", "C3": "evidence", "C4": "parser", "C5": "risk", "C6": "authority",
           "C7": "gate", "C8": "probe", "C9": "ledger", "C10": "sentinel", "C11": "twin", "C12": "audit",
           "C13": "console", "C14": "labelstore", "C15": "bench"}

# (id, from, to, message, note)
EDGES = [
    ("H01", "C11", "C1", "AudioFrame", "synthetic caller audio over WebSocket"),
    ("H02", "C13", "C1", "AudioFrame", "browser softphone (live demo)"),
    ("H03", "C1", "C2", "AudioFrame", "tap of the same frames"),
    ("H04", "C2", "C1", "ChannelProfile", "per turn at EOU"),
    ("H05", "C1", "C3", "TurnInput", "per turn; includes asr, profile, dialogue slice, ledger view, clock"),
    ("H06", "C3", "C4", "EvidenceBundle", "mode NEW_COMMAND"),
    ("H07", "C3", "C8", "EvidenceBundle", "mode PROBE_ANSWER / UNDO_WINDOW"),
    ("H08", "C4", "C5", "ActionProposal", "EB passed by reference"),
    ("H09", "C8", "C5", "ProbeOutcome", "rescoring after a probe answer"),
    ("H10", "C5", "C7", "ScoredProposal", "carries EB and AP"),
    ("H11", "C7", "C12", "AuditRecord", "DECISION; durable ack before any commit"),
    ("H12", "C7", "C9", "CommitRequest", "COMMIT only; carries audit seq+hash"),
    ("H13", "C7", "C8", "Decision", "PROBE / REPROMPT / ESCALATE / REFUSE"),
    ("H14", "C7", "C13", "EscalationTicket", "ESCALATE only"),
    ("H15", "C9", "C12", "AuditRecord", "LEDGER_EVENT / SESSION_EVENT"),
    ("H16", "C9", "C8", "Receipt", "PENDING / POSTED / UNDONE content for speech"),
    ("H17", "C9", "C1", "LedgerView", "session open and after each entry"),
    ("H18", "C9", "C13", "LedgerView", "display stream"),
    ("H19", "C1", "C9", "LedgerSignal", "SESSION_OPEN/CLOSE, RECEIPT_DELIVERED/FAILED"),
    ("H20", "C8", "C9", "UndoRequest", "caller said galat inside the undo window"),
    ("H21", "C8", "C1", "SpeechPlan", "everything the system says"),
    ("H22", "C1", "C11", "CallControl", "SpeechPlan.expects, so the Twin answers correctly"),
    ("H23", "C1", "C13", "CallControl", "UI display / softphone prompts"),
    ("K01", "C12", "C14", "AuditRecord", "audit stream for joins"),
    ("K02", "C12", "C10", "AuditRecord", "audit stream for M1"),
    ("K03", "C12", "C13", "AuditRecord", "audit stream for display"),
    ("K04", "C12", "C15", "AuditRecord", "replay input"),
    ("K05", "C11", "C14", "GoldLabel", "ground truth per call"),
    ("K06", "C13", "C14", "HumanVerdict", "operator-resolved escalation (Tier A)"),
    ("K07", "C13", "C9", "HumanCommit", "operator-resolved escalation"),
    ("K08", "C13", "C11", "ScenarioCommand", "start/stop, inject shifts, canary rate"),
    ("K09", "C14", "C6", "CalibrationRecord", "Calib split only"),
    ("K10", "C14", "C5", "TrainingData", "Train/Valid splits"),
    ("K11", "C14", "C3", "TrainingData", "Train split: confusion tables, repair weights"),
    ("K12", "C14", "C8", "TrainingData", "Train split: probe response models"),
    ("K13", "C14", "C10", "LabeledOutcome", "gold/audit labeled outcomes for M2"),
    ("K14", "C14", "C10", "CalibrationRecord", "reference scores for M1"),
    ("K15", "C14", "C11", "CellStats", "calibration deficits"),
    ("K16", "C14", "C13", "CellStats", "display"),
    ("K17", "C14", "C15", "TestSplit", "custody API; logged"),
    ("K18", "C5", "C6", "ScorerRelease", "staged scorer for certification"),
    ("K19", "C6", "C5", "RegistrySnapshot", "active scorer_version binding"),
    ("K20", "C6", "C7", "RegistrySnapshot", "atomic pointer; thresholds and lease"),
    ("K21", "C6", "C10", "RegistrySnapshot", "detects new certificate -> reset detectors"),
    ("K22", "C6", "C13", "RegistrySnapshot", "display"),
    ("K23", "C6", "C15", "RegistrySnapshot", "replay input"),
    ("K24", "C6", "C12", "AuditRecord", "REGISTRY_EVENT"),
    ("K25", "C10", "C6", "SentinelEvent", "state changes, shift onset, heartbeat"),
    ("K26", "C10", "C13", "SentinelEvent", "display"),
    ("K27", "C10", "C12", "AuditRecord", "SENTINEL_EVENT"),
    ("K28", "C10", "C11", "CanaryRequest", "accelerate labels for a cell"),
    ("K29", "C9", "C12", "AuditQuery", "verify audit seq+hash before PREPARE"),
    ("K30", "C9", "C7", "HealthSignal", "ledger_healthy"),
    ("K31", "C12", "C7", "HealthSignal", "audit_healthy"),
    ("K32", "C15", "C13", "EvalReport", "display"),
    ("K33", "C15", "C11", "ScenarioCommand", "fresh shift-test and live-run batches"),
    ("X01", "C1", "PRISMA", "PrismaStream", "hosted STT, streaming"),
    ("X02", "PRISMA", "C1", "PrismaStream", "partials/finals with word timing and confidence"),
    ("X03", "C4", "EVON", "EvonRequest", "only when Tier 1 is ambiguous"),
    ("X04", "EVON", "C4", "EvonResponse", "constrained JSON"),
    ("X05", "C1", "TIMBRE", "TtsRequest", "hosted TTS, streaming"),
    ("X06", "TIMBRE", "C1", "TtsAudio", "synthesized audio"),
    ("X07", "C11", "TIMBRE", "TtsRequest", "caller voices for the Twin"),
    ("X08", "TIMBRE", "C11", "TtsAudio", "synthesized caller audio"),
    ("X09", "C11", "EVON", "EvonRequest", "carrier-phrase paraphrase (offline)"),
    ("X10", "EVON", "C11", "EvonResponse", "paraphrase"),
]

# Offline library edges (C15 step 6): bench imports the pure functions of these packages without runtime messages.
OFFLINE_IMPORTS = {"bench": {"evidence", "risk", "gate", "probe"}}
ADAPTER_USERS = {"gateway", "parser", "twin"}  # TP5


def _cnum(c: str) -> int:
    return int(c[1:])


def gen_edges() -> list[str]:
    head = "ID   FROM    TO       MESSAGE           NOTE"
    rows = [f"{i:<4} {a:<7} {b:<8} {m:<17} {n}" for i, a, b, m, n in EDGES]
    return [head, *rows]


def gen_interfaces() -> list[str]:
    cons, prod = defaultdict(set), defaultdict(set)
    for _, a, b, m, _ in EDGES:
        prod[a].add(m)
        cons[b].add(m)
    out = []
    for c in sorted(COMPONENTS, key=_cnum):
        out.append(f"{c:<4} {COMPONENTS[c]}")
        out.append("       consumes: " + ", ".join(sorted(cons[c])))
        out.append("       produces: " + ", ".join(sorted(prod[c])))
    return out


def gen_matrix() -> list[str]:
    cs = sorted(COMPONENTS, key=_cnum)
    cell: dict[tuple[str, str], list[str]] = defaultdict(list)
    for i, a, b, _, _ in EDGES:
        if a in COMPONENTS and b in COMPONENTS:
            cell[(a, b)].append(i)
    lines = ["      " + "".join(f"{c:<8}" for c in cs)]
    for a in cs:
        lines.append(f"{a:<6}" + "".join(f"{'/'.join(sorted(cell[(a, b)])) or '.':<8}" for b in cs))
    return lines


def _norm(lines: list[str]) -> list[str]:
    return [" ".join(x.split()) for x in lines if x.strip()]


def check_tp() -> list[str]:
    errs: list[str] = []
    by_msg_from, by_msg_to = defaultdict(set), defaultdict(set)
    for _, a, b, m, _ in EDGES:
        by_msg_from[m].add(a)
        by_msg_to[m].add(b)
    ids = [e[0] for e in EDGES]
    if len(set(ids)) != len(ids) or len(EDGES) != 66:
        errs.append(f"expected 66 unique edges, got {len(EDGES)}")
    internal = [e for e in EDGES if e[1] in COMPONENTS and e[2] in COMPONENTS]
    if len(internal) != 56:
        errs.append(f"expected 56 internal edges, got {len(internal)}")
    if by_msg_from["CommitRequest"] != {"C7"} or by_msg_to["CommitRequest"] != {"C9"}:
        errs.append("TP1: CommitRequest must go C7 -> C9 only")
    if {a for _, a, b, _, _ in EDGES if b == "C9"} != {"C7", "C13", "C8", "C1"}:
        errs.append("TP2: inbound sources of C9 must be exactly C7, C13, C8, C1")
    if {a for _, a, b, m, _ in EDGES if m == "AuditRecord" and b == "C12"} != {"C7", "C9", "C6", "C10"}:
        errs.append("TP3: audit writers must be C7, C9, C6, C10 into C12 only")
    if by_msg_from["RegistrySnapshot"] != {"C6"}:
        errs.append("TP3: only C6 publishes RegistrySnapshot")
    if by_msg_to["TestSplit"] != {"C15"}:
        errs.append("TP3: only C15 consumes TestSplit")
    have = {(a, b, m) for _, a, b, m, _ in EDGES}
    if ("C7", "C12", "AuditRecord") not in have or ("C9", "C12", "AuditQuery") not in have:
        errs.append("TP4: audit-before-act edges missing (H11, K29)")
    prov = {(a if b in PROVIDERS else b, p) for _, a, b, _, _ in EDGES for p in (a, b) if p in PROVIDERS}
    if prov != {("C1", "PRISMA"), ("C1", "TIMBRE"), ("C4", "EVON"), ("C11", "TIMBRE"), ("C11", "EVON")}:
        errs.append(f"TP5: provider edges {sorted(prov)}")
    g = defaultdict(set)
    inbound, outbound = set(), set()
    for _, a, b, _, _ in EDGES:
        if a in COMPONENTS and b in COMPONENTS:
            g[a].add(b)
            g[b].add(a)
            outbound.add(a)
            inbound.add(b)
    seen, stack = set(), ["C1"]
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(g[n])
    if seen != set(COMPONENTS):
        errs.append(f"TP6: graph not connected, unreachable {sorted(set(COMPONENTS) - seen)}")
    for c in COMPONENTS:
        if c not in inbound or c not in outbound:
            errs.append(f"TP6: {c} lacks an inbound or outbound edge")
    for m in by_msg_from:
        if not by_msg_from[m] or not by_msg_to[m]:
            errs.append(f"TP7: {m} lacks producer or consumer")
    chain = [("C1", "C3"), ("C3", "C4"), ("C4", "C5"), ("C5", "C7"), ("C7", "C9"), ("C9", "C8"), ("C8", "C1"),
             ("C3", "C8"), ("C8", "C5"), ("C7", "C8")]
    pairs = {(a, b) for _, a, b, _, _ in EDGES}
    errs += [f"TP8: hot-path edge {a}->{b} missing" for a, b in chain if (a, b) not in pairs]
    return errs


def check_types() -> list[str]:
    sys.path.insert(0, str(ROOT / "src"))
    from lexi.common import types as t

    declared = {n for n, o in vars(t).items() if isinstance(o, type) and issubclass(o, t.Msg) and o is not t.Msg}
    wanted = {m for _, _, _, m, _ in EDGES}
    errs = [f"types.py lacks message {m}" for m in sorted(wanted - declared)]
    errs += [f"types.py declares {m} which has no edge" for m in sorted(declared - wanted)]
    return errs


def check_imports() -> list[str]:
    allowed = {pkg: {"common", pkg} | OFFLINE_IMPORTS.get(pkg, set()) for pkg in PACKAGE.values()}
    for _, a, b, _, _ in EDGES:
        if a in PACKAGE:
            if b in PACKAGE:
                allowed[PACKAGE[a]].add(PACKAGE[b])
            elif b in PROVIDERS:
                allowed[PACKAGE[a]].add("adapters")
    allowed["adapters"] = {"common", "adapters"}
    errs = []
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).parts
        if len(rel) < 2:
            continue  # lexi/__init__.py and cli.py are the composition root
        pkg = rel[0]
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            mods += [a.name for a in node.names] if isinstance(node, ast.Import) else []
            for mod in mods:
                parts = mod.split(".")
                if parts[0] != "lexi" or len(parts) < 2 or parts[1] == pkg:
                    continue
                if parts[1] not in allowed.get(pkg, set()):
                    errs.append(f"{f.relative_to(ROOT)}: imports lexi.{parts[1]} (not a port of {pkg}/)")
                if parts[1] == "adapters" and pkg not in ADAPTER_USERS:
                    errs.append(f"{f.relative_to(ROOT)}: TP5 only gateway/, parser/, twin/ may import adapters")
    return errs


def check_spec() -> list[str]:
    if not SPEC.exists():
        print("note: docs/LEXI_SPEC.txt not present; spec-vs-script equality check skipped")
        return []
    text = SPEC.read_text(encoding="utf-8").splitlines()

    def block(start: str, end: str) -> list[str]:
        i = next(k for k, ln in enumerate(text) if ln.startswith(start))
        j = next(k for k, ln in enumerate(text) if k > i and ln.startswith(end))
        return text[i + 1 : j]

    pairs = [("8.1", gen_edges(), block("8.1 Edge list", "8.2 Message contracts")),
             ("8.4", gen_interfaces(), block("8.4 Per-component", "8.5 Connectivity matrix")),
             ("8.5", gen_matrix(), [ln for ln in block("8.5 Connectivity matrix", "8.6 Invariants") if not ln.startswith("(")])]
    return [f"spec section {sec} differs from check_topology.py output" for sec, gen, spec in pairs if _norm(gen) != _norm(spec)]


def main() -> int:
    if "--print" in sys.argv:
        for title, lines in (("8.1", gen_edges()), ("8.4", gen_interfaces()), ("8.5", gen_matrix())):
            print(f"--- {title}")
            print("\n".join(lines))
        return 0
    errs = check_tp() + check_types() + check_imports() + check_spec()
    for e in errs:
        print("FAIL", e)
    if not errs:
        print(f"topology ok: {len(EDGES)} edges, TP1-TP8, 38 message types, import ports")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
