"""Scripts (C11 step 1): carrier phrase x party x amount x date, with gold. Values are inserted into the phrase after it is chosen, so gold is exact.

Synthetic registries live here too: a call needs an account whose parties the Twin can name. Everything is a pure function of its seed.
"""

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from lexi.common.hashing import digest
from lexi.twin.paraphrase import EvonCall, paraphrase
from lexi.twin.verbalize import (
    NATIVE,
    SpokenToken,
    lexicon,
    mid_number_pause,
    pick,
    repair_tokens,
    verbalize_amount,
    verbalize_date,
    words_to_tokens,
)

REFERENCE_TS = 1_790_000_000.0  # registry history is dated from here, so every process that calls make_accounts gets the same accounts
AMOUNT_RANGE = (100, 25_000)  # C11 step 1: log-uniform; the gate's max autonomous amount is the upper end
P_REPAIR, P_MINIMAL_PAIR, P_PAUSE = 0.12, 0.12, 0.15  # rtp: repairs 12% (C11 step 1); with low-SNR calls the hard share is ~35% (C11 step 5)
INTENTS = {"T1": ("CREDIT_ENTRY", "DEBIT_ENTRY"), "T2": ("PROMISE_TO_PAY", "RESCHEDULE_DUE")}
NEEDS = {"CREDIT_ENTRY": ("party", "amount"), "DEBIT_ENTRY": ("party", "amount"), "PROMISE_TO_PAY": ("party", "amount", "date"), "RESCHEDULE_DUE": ("party", "date")}

# (latin, native script). A registry draws from the training pool; S4 NEW_PARTIES draws from the unseen pool, so its names never occur in Train or Calib.
NAMES: dict[str, dict[str, list[tuple[str, str]]]] = {
    "hi": {"train": [("Sharma", "शर्मा"), ("Verma", "वर्मा"), ("Gupta", "गुप्ता"), ("Singh", "सिंह"), ("Yadav", "यादव"), ("Mishra", "मिश्रा"),
                     ("Tiwari", "तिवारी"), ("Pandey", "पांडे"), ("Agarwal", "अग्रवाल"), ("Jain", "जैन"), ("Khanna", "खन्ना"), ("Kapoor", "कपूर"),
                     ("Malhotra", "मल्होत्रा"), ("Chauhan", "चौहान"), ("Joshi", "जोशी"), ("Rawat", "रावत"), ("Saxena", "सक्सेना"),
                     ("Srivastava", "श्रीवास्तव"), ("Rastogi", "रस्तोगी"), ("Mehta", "मेहता"), ("Patel", "पटेल"), ("Thakur", "ठाकुर"),
                     ("Dubey", "दुबे"), ("Sinha", "सिन्हा"), ("Kumar", "कुमार"), ("Chopra", "चोपड़ा"), ("Bansal", "बंसल"), ("Goel", "गोयल"),
                     ("Mittal", "मित्तल"), ("Arora", "अरोड़ा")],
           "unseen": [("Awasthi", "अवस्थी"), ("Dwivedi", "द्विवेदी"), ("Nigam", "निगम"), ("Bajaj", "बजाज"), ("Walia", "वालिया"), ("Dhingra", "ढींगरा"),
                      ("Luthra", "लूथरा"), ("Sethi", "सेठी"), ("Tandon", "टंडन"), ("Bhalla", "भल्ला")]},
    "ta": {"train": [("Murugan", "முருகன்"), ("Selvam", "செல்வம்"), ("Kumar", "குமார்"), ("Raja", "ராஜா"), ("Suresh", "சுரேஷ்"), ("Ramesh", "ரமேஷ்"),
                     ("Senthil", "செந்தில்"), ("Karthik", "கார்த்திக்"), ("Anand", "ஆனந்த்"), ("Vijay", "விஜய்"), ("Ganesh", "கணேஷ்"), ("Mani", "மணி"),
                     ("Balu", "பாலு"), ("Devi", "தேவி"), ("Lakshmi", "லட்சுமி"), ("Meena", "மீனா"), ("Priya", "பிரியா"), ("Saravanan", "சரவணன்"),
                     ("Pandian", "பாண்டியன்"), ("Velu", "வேலு"), ("Kannan", "கண்ணன்"), ("Shankar", "சங்கர்"), ("Ravi", "ரவி"), ("Chandran", "சந்திரன்"),
                     ("Nataraj", "நடராஜ்"), ("Palani", "பழனி"), ("Ramu", "ராமு"), ("Kuppusamy", "குப்புசாமி"), ("Sundaram", "சுந்தரம்"),
                     ("Mari", "மாரி")],
           "unseen": [("Azhagu", "அழகு"), ("Vadivel", "வடிவேல்"), ("Thangam", "தங்கம்"), ("Ponni", "பொன்னி"), ("Ilango", "இளங்கோ"), ("Muthu", "முத்து"),
                      ("Kathir", "கதிர்"), ("Boopathi", "பூபதி"), ("Arul", "அருள்"), ("Siva", "சிவா")]},
}
HONORIFICS: dict[str, list[tuple[str, str]]] = {
    "hi": [("ji", "जी"), ("ji", "जी"), ("bhai", "भाई"), ("sahab", "साहब"), ("seth", "सेठ")],
    "ta": [("anna", "அண்ணா"), ("anna", "அண்ணா"), ("akka", "அக்கா"), ("ayya", "ஐயா"), ("sir", "சார்")],
}

# C11 step 1: hand-written, >= 20 per intent per language. {PARTY} {AMOUNT} {DATE} each appear once. Words a number grammar would read
# ("do", "ek", "oru") are avoided on purpose: the caller says "kar lo", "jama karo".
CARRIERS: dict[tuple[str, str], list[str]] = {
    ("hi", "CREDIT_ENTRY"): [
        "{PARTY} ka {AMOUNT} aaya hai jama karo", "{PARTY} se {AMOUNT} mila jama karo", "{AMOUNT} jama karo {PARTY} ke naam", "{PARTY} ke {AMOUNT} jama karna",
        "{PARTY} ne {AMOUNT} bheje wo jama karo", "abhi {PARTY} ka {AMOUNT} aaya", "{AMOUNT} aaye {PARTY} ki taraf se jama kar lo",
        "{PARTY} ke {AMOUNT} mile hain entry karo", "{PARTY} ka {AMOUNT} jama kijiye", "{PARTY} ka paisa {AMOUNT} aaya hai",
        "{PARTY} se {AMOUNT} received hue jama karo", "{AMOUNT} {PARTY} ke account mein jama", "{PARTY} ne abhi {AMOUNT} diye hain jama karo",
        "{PARTY} ka {AMOUNT} aaya wo jama mein likho", "dekho {PARTY} ka {AMOUNT} aa gaya jama karo", "{PARTY} ke yahan se {AMOUNT} mile jama karo",
        "{AMOUNT} ka payment {PARTY} se mila jama karo", "{PARTY} ka {AMOUNT} jama ho gaya entry karo", "{PARTY} ke {AMOUNT} mile hain jama karna",
        "{PARTY} ne {AMOUNT} bhej diye jama karo", "entry jama ki {PARTY} ka {AMOUNT} aaya", "{PARTY} ka {AMOUNT} aaya jama kar lo"],
    ("hi", "DEBIT_ENTRY"): [
        "{PARTY} ko {AMOUNT} udhaar likho", "{PARTY} ka {AMOUNT} udhaar", "{PARTY} ke naam {AMOUNT} udhaar likho", "{PARTY} ko maal diya {AMOUNT} ka udhaar",
        "{PARTY} ka {AMOUNT} baaki likho", "{AMOUNT} udhaar {PARTY} ko diye", "{PARTY} ko {AMOUNT} ka maal gaya udhaar mein",
        "{PARTY} ke khate mein {AMOUNT} udhaar jodo", "abhi {PARTY} ko {AMOUNT} udhar diye", "{PARTY} ka {AMOUNT} baaki hai likh lo",
        "{PARTY} ke {AMOUNT} udhaar mein likhna", "{AMOUNT} ka udhaar {PARTY} ke naam karo", "{PARTY} ne {AMOUNT} ka maal liya udhaar likho",
        "{PARTY} ko {AMOUNT} diye hain baaki mein likho", "{PARTY} ka {AMOUNT} udhaar chadhao", "{PARTY} ki taraf {AMOUNT} baaki likho",
        "{PARTY} ko {AMOUNT} udhaar diya entry karo", "dekho {PARTY} ka {AMOUNT} udhaar likhna", "{PARTY} ke {AMOUNT} udhaar mein jodo",
        "{PARTY} ko {AMOUNT} ka saman diya baaki likho", "{AMOUNT} udhaar {PARTY} ka", "{PARTY} ko {AMOUNT} ka maal diya baaki mein"],
    ("hi", "PROMISE_TO_PAY"): [
        "{PARTY} ne vaada kiya {DATE} tak {AMOUNT} denge", "{PARTY} {DATE} tak {AMOUNT} dega", "{PARTY} ka vaada hai {AMOUNT} {DATE} tak denge",
        "{PARTY} bola {DATE} tak {AMOUNT} bhejenge", "{PARTY} ne promise kiya {AMOUNT} {DATE} tak", "{DATE} tak {PARTY} {AMOUNT} denge likh lo",
        "{PARTY} {AMOUNT} {DATE} tak vaada hai denge", "note karo {PARTY} {AMOUNT} {DATE} tak karenge", "{PARTY} ne bola {AMOUNT} {DATE} tak denge",
        "{PARTY} ka vaada {DATE} tak {AMOUNT}", "{PARTY} ne kaha {AMOUNT} denge {DATE} tak", "vaada hai {PARTY} ka {DATE} tak {AMOUNT} denge",
        "{PARTY} {AMOUNT} bhejega {DATE} tak", "{PARTY} ki promise {DATE} tak {AMOUNT} denge", "{PARTY} se baat hui {DATE} tak {AMOUNT} denge",
        "{PARTY} {DATE} tak {AMOUNT} karenge pakka", "{PARTY} ne wada kiya {AMOUNT} {DATE} tak denge", "likh lo {PARTY} {DATE} tak {AMOUNT} denge",
        "{PARTY} ka {AMOUNT} {DATE} tak milega vaada hai", "{PARTY} {AMOUNT} {DATE} tak denge promise", "{PARTY} ne {DATE} tak {AMOUNT} ka vaada kiya denge"],
    ("hi", "RESCHEDULE_DUE"): [
        "{PARTY} ki due date {DATE} tak badlo", "{PARTY} ka due {DATE} tak badhao", "{PARTY} ki date aage badhao {DATE} tak",
        "{PARTY} ka payment {DATE} tak shift karo", "{PARTY} ki date {DATE} karo aage badhao", "{PARTY} ka due {DATE} tak postpone karo",
        "{PARTY} ko {DATE} tak ka time dena date badlo", "{PARTY} ka due {DATE} tak extend karo", "{PARTY} ki date {DATE} tak badal dena",
        "{PARTY} ka due date {DATE} tak badlo", "{PARTY} ki payment {DATE} tak postpone karo", "{PARTY} ne bola date {DATE} tak badhao",
        "{PARTY} ka due {DATE} tak taalo", "{DATE} tak {PARTY} ka due badlo", "{PARTY} ki date {DATE} tak badhao", "{PARTY} ka due date {DATE} tak shift karna",
        "{PARTY} ko thoda time dena due {DATE} tak badlo", "{DATE} tak {PARTY} ka due taalo", "{PARTY} ki date {DATE} tak extend karna",
        "due date badlo {PARTY} ki {DATE} tak", "{PARTY} ka due {DATE} tak badhao"],
    ("ta", "CREDIT_ENTRY"): [
        "{PARTY} oda {AMOUNT} vandhuchu jama pannunga", "{PARTY} kitta {AMOUNT} vandhuchu", "{PARTY} {AMOUNT} kuduthaanga jama pannunga",
        "{AMOUNT} jama podunga {PARTY} oda account la", "{PARTY} oda {AMOUNT} kedachuchu jama pannunga", "ippo {PARTY} oda {AMOUNT} vandhuchu",
        "{PARTY} {AMOUNT} anuppitaanga vandhuchu jama pannunga", "{PARTY} oda {AMOUNT} received entry pannunga", "{PARTY} kitta {AMOUNT} kedachuchu entry podunga",
        "{AMOUNT} vandhuchu {PARTY} oda jama pannunga", "{PARTY} oda panam {AMOUNT} vandhuchu", "{PARTY} oda {AMOUNT} jama ezhuthunga",
        "please {PARTY} oda {AMOUNT} jama pannunga", "{PARTY} {AMOUNT} vandhadhu jama podunga", "{PARTY} kitta {AMOUNT} received pannunga jama",
        "{PARTY} oda {AMOUNT} kidaichadhu jama pannunga", "{PARTY} account la {AMOUNT} jama pannunga", "{AMOUNT} {PARTY} kitta irundhu kedachuchu",
        "{PARTY} oda {AMOUNT} vandhuchu ezhuthunga", "{PARTY} oda {AMOUNT} vanthuchu jama pannu", "ippo {PARTY} kitta {AMOUNT} kedachuchu jama podunga"],
    ("ta", "DEBIT_ENTRY"): [
        "{PARTY} kku {AMOUNT} kadan ezhuthunga", "{PARTY} oda {AMOUNT} kadan", "{PARTY} kku {AMOUNT} kadanaa kuduthen", "{PARTY} oda {AMOUNT} baaki ezhuthunga",
        "{AMOUNT} udhaar {PARTY} kku podunga", "{PARTY} kku {AMOUNT} maal kuduthen kadan", "{PARTY} oda {AMOUNT} kadan podunga",
        "ippo {PARTY} kku {AMOUNT} kadanaa kuduthen", "{PARTY} oda {AMOUNT} baaki irukku ezhuthunga", "{PARTY} kku {AMOUNT} udhaar pannunga",
        "{AMOUNT} kadan {PARTY} oda account la podunga", "{PARTY} kitta {AMOUNT} baaki ezhuthunga", "{PARTY} kku {AMOUNT} koduthaen kadan ezhuthunga",
        "{PARTY} oda kadan {AMOUNT} podunga", "{PARTY} kku maal {AMOUNT} kadanaa pannunga", "{PARTY} oda {AMOUNT} paakki ezhuthunga",
        "please {PARTY} oda {AMOUNT} kadan pottu vainga", "{PARTY} kku {AMOUNT} kuduthen udhaar entry podunga", "{AMOUNT} {PARTY} oda kadan",
        "{PARTY} oda {AMOUNT} baaki podunga", "ezhuthunga {PARTY} kku {AMOUNT} kadan"],
    ("ta", "PROMISE_TO_PAY"): [
        "{PARTY} {DATE} {AMOUNT} tharuvaanga", "{PARTY} vaakku kuduthaanga {DATE} {AMOUNT} tharuvaanga", "{PARTY} oda promise {DATE} {AMOUNT}",
        "{PARTY} {AMOUNT} {DATE} kudupaanga", "{DATE} {PARTY} {AMOUNT} tharuvaanga ezhuthunga", "{PARTY} sonnaanga {AMOUNT} {DATE} tharuvaanga",
        "{PARTY} {AMOUNT} {DATE} tharen nu sonnaanga", "{PARTY} oda vaakku {DATE} {AMOUNT}", "note pannunga {PARTY} {DATE} {AMOUNT} tharuvaanga",
        "{PARTY} promise pannaanga {AMOUNT} {DATE}", "{PARTY} oda {AMOUNT} {DATE} kudupaanga vaakku", "{PARTY} {DATE} {AMOUNT} kudupaanga nu promise",
        "ippo {PARTY} {AMOUNT} {DATE} tharuvaanga", "{PARTY} kitta pesinen {AMOUNT} {DATE} tharuvaanga", "{DATE} {AMOUNT} {PARTY} oda vaakku",
        "{PARTY} {AMOUNT} {DATE} tharuvaanga please ezhuthunga", "{PARTY} {AMOUNT} tharuven {DATE} nu sonnaanga", "{PARTY} {DATE} {AMOUNT} kudupaanga vaakku",
        "{PARTY} oda promise {AMOUNT} {DATE}", "{PARTY} {AMOUNT} tharuvaanga {DATE}"],
    ("ta", "RESCHEDULE_DUE"): [
        "{PARTY} oda due {DATE} ku maathunga", "{PARTY} oda date {DATE} ku thalli podunga", "{PARTY} due date {DATE} postpone pannunga",
        "{PARTY} oda due {DATE} extend pannunga", "{DATE} ku {PARTY} oda due maathu", "{PARTY} oda payment date {DATE} ku maathunga",
        "{PARTY} kku due {DATE} ku thalli podunga", "{PARTY} oda due {DATE} ku maatru", "please {PARTY} oda due {DATE} extend pannunga",
        "{PARTY} date {DATE} ku postpone podunga", "{DATE} ku {PARTY} due thalli podunga", "{PARTY} oda due date {DATE} maatru",
        "{PARTY} oda due {DATE} ku thalli", "{PARTY} oda payment {DATE} ku postpone pannunga", "{DATE} ku {PARTY} date maathunga",
        "{PARTY} due {DATE} ku extend podunga", "{PARTY} oda due date {DATE} ku maathunga", "ippo {PARTY} oda due {DATE} ku thalli podunga",
        "{PARTY} oda due {DATE} ku postpone", "{PARTY} date {DATE} ku maatru"],
}


@dataclass(frozen=True)
class PartySpec:
    id: str
    display: str  # as C9 shows and speaks it: "Sharma Ji"
    aliases: tuple[str, ...]
    name: str
    native: str
    honorific: tuple[str, str]  # (latin, native)
    balance: int
    amount_hist: tuple[int, ...]
    last_entry_ts: float


@dataclass(frozen=True)
class AccountSpec:
    account: str
    lang: str
    parties: tuple[PartySpec, ...]

    def ledger_row(self) -> dict[str, Any]:
        """The shape Ledger.seed takes, minus `ipk` (the composition root adds it from C3)."""
        return {"account": self.account, "lang": self.lang, "parties": [
            {"id": p.id, "display": p.display, "aliases": list(p.aliases), "balance": p.balance, "amount_hist": list(p.amount_hist),
             "last_entry_ts": p.last_entry_ts} for p in self.parties]}


@dataclass(frozen=True)
class Script:
    script_id: str
    lang: str
    account: str
    intent: str
    party_id: str
    amount: int | None
    date: str | None
    tags: tuple[str, ...]
    tokens: tuple[SpokenToken, ...]
    gold: dict[str, Any]  # canonical: intent, party, amount, date (the slots this intent needs)


def make_accounts(seed: int, languages: Sequence[str], n_per_language: int = 6, parties_per_account: int = 10, *, unseen: bool = False) -> list[AccountSpec]:
    out = []
    for lang in languages:
        pool = NAMES[lang]["unseen" if unseen else "train"]
        for i in range(n_per_language):
            rng = random.Random(f"{seed}|{lang}|{i}|{unseen}")
            parties = []
            for j, (name, native) in enumerate(rng.sample(pool, min(parties_per_account, len(pool)))):
                hon = rng.choice(HONORIFICS[lang])
                typical = math.exp(rng.uniform(math.log(200), math.log(20_000)))
                hist = tuple(max(10, int(round(typical * rng.uniform(0.5, 1.5), -1))) for _ in range(rng.randint(3, 8)))
                parties.append(PartySpec(f"P{j + 1:02d}", f"{name} {hon[0].title()}", (name,), name, native, hon, rng.randrange(0, 60_000, 50), hist,
                                         REFERENCE_TS - rng.uniform(1, 40) * 86400))
            out.append(AccountSpec(f"{lang.upper()}-{'U' if unseen else 'A'}{i + 1:02d}", lang, tuple(parties)))
    return out


def party_tokens(p: PartySpec, lang: str, rng: random.Random, honorific: bool = True) -> list[SpokenToken]:
    script = NATIVE[lang]
    name = SpokenToken(p.name, "party", {"latin": (p.name,), script: (p.native,)})
    hon = SpokenToken(p.honorific[0], "honorific", {"latin": (p.honorific[0],), script: (p.honorific[1],)})
    return [name, hon] if honorific and rng.random() < 0.75 else [name]


# ---------------------------------------------------------------------------- amounts
def draw_amount(rng: random.Random) -> int:
    """Log-uniform 100..25,000, rounded the way people round; 15% are not round at all."""
    v = math.exp(rng.uniform(math.log(AMOUNT_RANGE[0]), math.log(AMOUNT_RANGE[1])))
    step = 10 if rng.random() < 0.15 else 50 if v < 1000 else 100 if v < 10_000 else 500
    return min(max(round(v / step) * step, AMOUNT_RANGE[0]), AMOUNT_RANGE[1])


def minimal_pair(lang: str, rng: random.Random) -> tuple[int, list[SpokenToken]]:
    """An amount built around one member of a confusable group (satrah / sattar, paintees / paintalis, dhai / do), so the confusable word is certainly spoken."""
    lex = lexicon(lang)
    members = {m for g in lex.groups for m in g}
    nums = [(v, f) for v, f in lex.num.items() if f["latin"][0] in members]
    fracs = [(k, f) for k, f in lex.frac.items() if f["latin"][0] in members]
    kind, key, forms = rng.choice([("num", v, f) for v, f in nums] + [("frac", k, f) for k, f in fracs])
    forms = {**forms, "latin": forms["latin"][:1]}  # the group member itself, not another spelling
    if kind == "frac":  # dhai / dedh before a scale
        scale = rng.choice([100, 1000])
        return int(lex.frac_value[key] * scale), pick(forms, "frac", rng) + pick(lex.scale[scale], "scale", rng)
    value = int(key)
    if value >= 200:  # Tamil fused hundred, after any thousands
        base = rng.randrange(0, 21) * 1000
        return base + value, (verbalize_amount(base, lang, rng) if base else []) + pick(forms, "num", rng)
    mode = rng.choice(["tail", *(["thousand"] if value <= 25 else []), *(["hundred"] if lang == "hi" else [])])
    if mode != "tail":
        scale = 1000 if mode == "thousand" else 100
        return value * scale, pick(forms, "num", rng) + pick(lex.scale[scale], "scale", rng)
    base = rng.randrange(1, 25) * 100 if rng.random() < 0.5 else rng.randrange(1, 25) * 1000 + rng.randrange(0, 10) * 100
    return base + value, verbalize_amount(base, lang, rng) + pick(forms, "num", rng)


# ---------------------------------------------------------------------------- scripts
def _fill(template: str, lang: str, values: Mapping[str, list[SpokenToken]]) -> list[SpokenToken]:
    out: list[SpokenToken] = []
    for word in template.split():
        key = word.strip("{}")
        out += values[key] if word.startswith("{") else words_to_tokens(word, lang)
    return out


def _other_amount(n: int, rng: random.Random) -> int:
    return next(a for a in (draw_amount(rng) for _ in range(20)) if a != n)


def make_script(rng: random.Random, accounts: Sequence[AccountSpec], lang: str, stratum: str, today: date,
                templates: Mapping[tuple[str, str], Sequence[str]] = CARRIERS, tier: str | None = None) -> Script:
    """stratum: rtp (the Reference Traffic Profile: 60% T1 / 40% T2, repairs, confusable amounts, mid-number pauses) | minimal_pair | plain.
    `tier` pins the action tier when a particular cell is wanted."""
    acct = rng.choice([a for a in accounts if a.lang == lang])
    tier = tier or ("T1" if rng.random() < 0.6 else "T2")
    intent = rng.choice([i for i in INTENTS[tier] if stratum != "minimal_pair" or "amount" in NEEDS[i]] or INTENTS["T1"])
    party = rng.choice(acct.parties)
    needs = NEEDS[intent]
    rtp = stratum == "rtp"
    mp = "amount" in needs and (stratum == "minimal_pair" or (rtp and rng.random() < P_MINIMAL_PAIR))
    amount_tokens: list[SpokenToken] = []
    amount: int | None = None
    if mp:
        amount, amount_tokens = minimal_pair(lang, rng)
    elif "amount" in needs:
        amount = draw_amount(rng)
        amount_tokens = verbalize_amount(amount, lang, rng)
    d = today + timedelta(days=rng.choice([rng.randint(1, 3), rng.randint(4, 7), rng.randint(8, 45)])) if "date" in needs else None
    template = rng.choice(list(templates[(lang, intent)]))
    date_tokens = verbalize_date(d, today, lang, rng, ko="{DATE} tak" not in template) if d else []
    party_toks = party_tokens(party, lang, rng)
    tags = ["minimal_pair"] if mp else []
    repair = rtp and rng.random() < P_REPAIR
    if repair:  # "<wrong> matlab <right>": the post-repair reading is the gold
        slot = rng.choices([s for s in needs], [{"party": 25, "amount": 60, "date": 15}[s] for s in needs])[0]
        marker = repair_tokens(lang, rng)
        if slot == "amount" and amount is not None:
            amount_tokens = verbalize_amount(_other_amount(amount, rng), lang, rng) + marker + amount_tokens
        elif slot == "party":
            wrong = rng.choice([p for p in acct.parties if p.id != party.id])
            party_toks = party_tokens(wrong, lang, rng) + marker + party_toks
        elif d:
            date_tokens = verbalize_date(today + timedelta(days=rng.randint(1, 45)), today, lang, rng, ko=False) + marker + date_tokens
        tags.append("repair")
    if "amount" in needs and rtp and rng.random() < P_PAUSE:
        paused = mid_number_pause(amount_tokens, rng.randrange(400, 1001, 100))
        if paused != amount_tokens:
            amount_tokens, tags = paused, [*tags, "pause"]
    tokens = _fill(template, lang, {"PARTY": party_toks, "AMOUNT": amount_tokens, "DATE": date_tokens})
    if rtp and rng.random() < 0.08:  # a filler before the command
        tokens = pick({"latin": ["um", "uh", "hmm"]}, "filler", rng) + tokens
    if tags:
        tags.append("hard")
    gold = {"intent": intent, "party": party.id, **({"amount": amount} if amount is not None else {}), **({"date": d.isoformat()} if d else {})}
    sid = "S-" + digest([lang, acct.account, gold, template, [t.w for t in tokens], tags])[:12]
    return Script(sid, lang, acct.account, intent, party.id, amount, d.isoformat() if d else None, tuple(tags), tuple(tokens), gold)


def restate(script: Script, party: PartySpec, slots: Sequence[str], today: date, rng: random.Random) -> list[SpokenToken]:
    """The caller says a value again (a re-ask, a correction, a contrast answer): the gold value only, no repair, no pause."""
    lang, out = script.lang, []
    for slot in slots:
        if slot == "party":
            out += party_tokens(party, lang, rng)
        elif slot == "amount" and script.amount is not None:
            out += verbalize_amount(script.amount, lang, rng)
        elif slot == "date" and script.date is not None:
            out += verbalize_date(date.fromisoformat(script.date), today, lang, rng)
    return out


def recommand(script: Script, party: PartySpec, today: date, rng: random.Random,
              templates: Mapping[tuple[str, str], Sequence[str]] = CARRIERS) -> list[SpokenToken]:
    """The whole command again after a re-prompt: a fresh carrier phrase, plain forms, same gold."""
    lang = script.lang
    values = {"PARTY": party_tokens(party, lang, rng), "AMOUNT": restate(script, party, ["amount"], today, rng), "DATE": restate(script, party, ["date"], today, rng)}
    return _fill(rng.choice(list(templates[(lang, script.intent)])), lang, values)


async def template_pool(evon: EvonCall | None, base: Mapping[tuple[str, str], Sequence[str]] = CARRIERS) -> dict[tuple[str, str], list[str]]:
    """Carrier phrases plus their accepted Evon paraphrases (C11 step 1). A paraphrase that loses or repeats a placeholder was already refused."""
    if evon is None:
        return {k: list(v) for k, v in base.items()}
    return {k: sorted({*v, *[await paraphrase(t, evon) for t in v]}, key=lambda t: (t not in v, t)) for k, v in base.items()}
