#!/usr/bin/env python3
"""Behavioural probe for the SD agent.

Drives scripted multi-turn conversations through the live HTTP API and reports
what the model and server actually sent. There is no test suite, so this is how
behavioural bugs get found.

    .venv/bin/python qa_probe.py --scenario qa/scenarios/counts.json
    .venv/bin/python qa_probe.py --scenario qa/scenarios/counts.json \
        --baseline qa/runs/<old>.json          # diff against an earlier run
    .venv/bin/python qa_probe.py --list-checks

A scenario is JSON: {"conversations": [{"id", "char", "turns": [...]}]}.
Turns are written in the user's own register — lowercase, terse, *asterisk
actions* — because that is what the model is tuned on.

Checks live in CHECKS below and return {name: detail} for anything they think
is wrong, or {} when clean. Add a check by writing a function that takes
(prompt, negative, turn, prev_prompt) and returns a dict.

Scoring rules learned the hard way, do not regress them:
  - a non-empty list is truthy, so multi-tag checks must test len() > 1
  - match whole comma-separated segments; "extreme close-up" CONTAINS "close-up"
  - "solo, 1boy" and "duo, 1girl, 1boy" are internally consistent — compare the
    count WORD against the SUM of the numeric tags, never against one tag
"""
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

import requests

BASE = os.environ.get("SDAGENT_URL", "http://127.0.0.1:8000")
ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "qa" / "runs"

SCALE = {"extreme close-up", "close-up", "closeup", "close up", "medium shot",
         "cowboy shot", "full body", "wide shot"}
SOLO_COUNT = {"solo", "1boy", "1girl"}
COUNT_WORD = {"duo": 2, "trio": 3, "quad": 4}
GUARDS = ("pov cock", "another person's cock", "first person view")
GENITAL = ("penis", "cock", "pussy", "mound", "pussy mound")
CHEST = ("boobs", "breasts", "cleavage", "nipples", "nipple")
PROXIMITY = ("between his thighs", "between his legs", "thighs framing viewer",
             "kneeling between his thighs", "between viewer's arms",
             "close to camera", "filling frame")
# a contact that names a body part; stacking two on the same part is what the
# system prompt warns averages into extra hands
OWNSHIP = ("another's hand", "hand from offscreen", "pov hand", "hand on",
           "hand on another", "another's mouth", "mouth on another")


# --------------------------------------------------------------------- helpers
def tags(prompt):
    return [t.strip() for t in (prompt or "").split(",") if t.strip()]


def count_totals(prompt):
    """(people implied by a count word, sum of numeric count tags)."""
    word = total = 0
    for t in tags(prompt):
        low = t.lower()
        for w, n in COUNT_WORD.items():
            if re.search(rf"\b{w}\b", low):
                word = n
        total += sum(int(x) for x in
                     re.findall(r"(\d+)\s*(?:boys?|girls?|others?)", low))
    return word, total


def has_multi(prompt):
    word, total = count_totals(prompt)
    return word > 1 or total > 1


# ---------------------------------------------------------------------- checks
def check_count_conflict(prompt, negative, turn, prev):
    word, total = count_totals(prompt)
    if word and total and word != total:
        return {"count_conflict": f"'{word}' but numeric tags sum to {total}"}
    return {}


def check_scale_ladder(prompt, negative, turn, prev):
    found = [t.lower() for t in tags(prompt) if t.lower() in SCALE]
    if len(found) > 1:
        return {"two_scale_tags": found}
    return {}


def check_stale_scale(prompt, negative, turn, prev):
    """A scale tag changed without the previous one being removed."""
    if not prev:
        return {}
    was = [t.lower() for t in tags(prev) if t.lower() in SCALE]
    now = [t.lower() for t in tags(prompt) if t.lower() in SCALE]
    if was and now and was[0] != now[0] and was[0] in now:
        return {"stale_scale_kept": [was[0], now[0]]}
    return {}


def check_dup_tags(prompt, negative, turn, prev):
    seen, dup = set(), set()
    for t in (x.lower() for x in tags(prompt)):
        if len(t) < 3:
            continue
        if t in seen:
            dup.add(t)
        seen.add(t)
    # 1boy/1girl legitimately appear once per subject
    dup -= SOLO_COUNT
    return {"duplicate_tags": sorted(dup)} if dup else {}


def check_anatomy_guard(prompt, negative, turn, prev):
    """POV anatomy must be suppressed ONLY for genital focus near the viewer."""
    low = (prompt or "").lower()
    neg = (negative or "").lower()
    present = sum(1 for g in GUARDS if g in neg)
    genital = any(k in low for k in GENITAL)
    chest = any(k in low for k in CHEST)
    prox = any(k in low for k in PROXIMITY) or has_multi(prompt)
    if genital and prox and present < 3:
        return {"anatomy_guard_missing": f"{present}/3"}
    if not genital and present == 3:
        # chest/face/framing: dead weight, and "first person view" flattens
        return {"anatomy_guard_overapplied":
                "chest" if chest else "no genital focus"}
    return {}


def check_stacked_contact(prompt, negative, turn, prev):
    """Two ownership tags on the same body part average into extra limbs."""
    counts = {}
    for t in tags(prompt):
        low = t.lower()
        if not any(o in low for o in OWNERSHIP):
            continue
        m = re.search(r"\b(hand|hands|finger|fingers|mouth|arm|arms|leg|legs|"
                      r"thigh|thighs|breast|boob|butt|hip)\b", low)
        if m:
            counts.setdefault(m.group(1), []).append(t)
    bad = {k: v for k, v in counts.items() if len(v) > 1}
    return {"stacked_contact_tags": bad} if bad else {}


def check_seed_discipline(prompt, negative, turn, prev):
    """'use the same seed' must reuse it; other turns should not pin the last one.

    Reads the seed the generation ACTUALLY used rather than the tool-call
    arguments: the SSE `tool_start` event carries only {"type","name"}, so the
    requested args are not available from the stream at all.
    """
    user = (turn.get("user") or "").lower()
    asked = "same seed" in user or "same prompt" in user
    seed, prev_seed = turn.get("seed"), turn.get("prev_seed")
    if seed is None or prev_seed is None:
        return {}
    if asked and seed != prev_seed:
        return {"seed_not_reused": f"asked for the same seed, sent {seed} not {prev_seed}"}
    if not asked and seed == prev_seed:
        return {"seed_pinned_unasked": seed}
    return {}


CHECKS = {
    "count_conflict": check_count_conflict,
    "scale_ladder": check_scale_ladder,
    "stale_scale": check_stale_scale,
    "dup_tags": check_dup_tags,
    "anatomy_guard": check_anatomy_guard,
    "stacked_contact": check_stacked_contact,
    "seed_discipline": check_seed_discipline,
}
DEFAULT_CHECKS = [c for c in CHECKS if c != "seed_discipline"]


# --------------------------------------------------------------------- client
class Agent:
    def __init__(self):
        self.s = requests.Session()

    def login(self):
        env = subprocess.run(
            ["systemctl", "show", "sd-agent.service",
             "--property=Environment", "--value"],
            capture_output=True, text=True).stdout
        m = re.search(r"AGENT_PASSWORD=(\S+)", env)
        if not m:
            sys.exit("could not read AGENT_PASSWORD from the unit")
        r = self.s.post(f"{BASE}/api/login", json={"password": m.group(1)},
                        timeout=15)
        r.raise_for_status()

    def post(self, path, payload=None):
        r = self.s.post(f"{BASE}{path}", json=payload or {}, timeout=60)
        r.raise_for_status()
        return r.json()

    def turn(self, text):
        """POST a message, drain the SSE stream, return the events."""
        events = []
        with self.s.post(f"{BASE}/api/chat", json={"message": text},
                         stream=True, timeout=900) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    try:
                        events.append(json.loads(line[5:].strip()))
                    except json.JSONDecodeError:
                        pass
        return events


# ----------------------------------------------------------------------- main
def run(scenario, checks, only=None, gallery=None, agent=None):
    agent = agent or Agent()
    agent.login()
    report = []
    for c in scenario["conversations"]:
        if only and c["id"] not in only:
            continue
        print(f"\n{'=' * 70}\n{c['id']}  ({c['char']})\n{'=' * 70}")
        agent.post("/api/character/select", {"id": c["char"]})
        agent.post("/api/chat/new")
        turns, prev, prev_seed = [], None, None
        for i, text in enumerate(c["turns"], 1):
            events = agent.turn(text)
            gen = next((e for e in events if e.get("type") == "generation"), None)
            rec = {"n": i, "user": text, "generated": bool(gen),
                   "prev_seed": prev_seed}
            if gen:
                g = gen["gen"]
                rec.update({
                    "files": gen.get("files"), "seed": gen.get("seed"),
                    "prompt": g.get("prompt"),
                    "negative_prompt": g.get("negative_prompt"),
                    "width": g.get("width"), "height": g.get("height"),
                    "steps": g.get("steps"), "cfg": g.get("cfg_scale"),
                    "sampler": g.get("sampler_name"),
                    "scheduler": g.get("scheduler"),
                    "elapsed": round(g.get("elapsed", 0), 2),
                    "findings": {},
                })
                for name in checks:
                    fn = CHECKS[name]
                    try:
                        out = fn(g.get("prompt"), g.get("negative_prompt"),
                                 rec, prev)
                    except Exception as e:                # noqa: BLE001
                        out = {"check_error": f"{name}: {type(e).__name__}: {e}"}
                    if out:
                        rec["findings"].update(out)
                prev = g.get("prompt")
                prev_seed = gen.get("seed")
                f = rec["findings"]
                print(f"  T{i} seed={gen['seed']:<11}"
                      f"{g.get('width')}x{g.get('height')} "
                      f"{g.get('steps')}st cfg{g.get('cfg_scale')} "
                      f"{'!! ' + json.dumps(f) if f else 'ok'}")
            else:
                print(f"  T{i} no-image")
            turns.append(rec)
            time.sleep(0.4)
        report.append({"id": c["id"], "char": c["char"], "turns": turns})
    if gallery:
        move_to_gallery(agent, gallery, report)
    return report


def move_to_gallery(agent, folder, report):
    rels = [t["files"][0].lstrip("/") for c in report for t in c["turns"]
            if t.get("files")]
    if not rels:
        return
    try:
        agent.post("/api/gallery/folder",
                   {"action": "move_images", "folder": folder, "rels": rels})
        print(f"\nmoved {len(rels)} images into {folder}/")
    except Exception as e:                            # noqa: BLE001
        print(f"\ngallery move failed: {type(e).__name__}: {e}")


def compare(report, baseline):
    def tally(rep):
        out = {}
        for c in rep:
            for t in c["turns"]:
                for k in (t.get("findings") or {}):
                    out[k] = out.get(k, 0) + 1
        gens = sum(1 for c in rep for t in c["turns"] if t.get("generated"))
        return out, gens

    b, bg = tally(baseline)
    a, ag = tally(report)
    print(f"\n{'finding':<28}{'baseline':>10}{'now':>8}")
    print("-" * 46)
    for k in sorted(set(b) | set(a)):
        print(f"{k:<28}{b.get(k, 0):>10}{a.get(k, 0):>8}")
    print(f"{'(generations)':<28}{bg:>10}{ag:>8}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", help="path to a scenario JSON")
    ap.add_argument("--baseline", help="earlier report JSON to diff against")
    ap.add_argument("--checks", help="comma-separated check names "
                                     f"(default: {','.join(DEFAULT_CHECKS)})")
    ap.add_argument("--only", help="comma-separated conversation ids to run")
    ap.add_argument("--out", help="where to write the report "
                                  "(default qa/runs/<timestamp>.json)")
    ap.add_argument("--gallery", help="move generated images into this "
                                       "gallery folder, e.g. qa-run-all")
    ap.add_argument("--list-checks", action="store_true")
    a = ap.parse_args()

    if a.list_checks:
        for name, fn in CHECKS.items():
            doc = (fn.__doc__ or "").strip().splitlines()
            print(f"  {name:<20} {doc[0] if doc else '(no description)'}")
        return
    if not a.scenario:
        ap.error("--scenario is required")

    checks = (a.checks.split(",") if a.checks else DEFAULT_CHECKS)
    unknown = [c for c in checks if c not in CHECKS]
    if unknown:
        sys.exit(f"unknown checks: {unknown}; try --list-checks")

    scenario = json.load(open(a.scenario))
    only = set(a.only.split(",")) if a.only else None
    report = run(scenario, checks, only, a.gallery)

    RUNS.mkdir(parents=True, exist_ok=True)
    out = Path(a.out) if a.out else RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1))
    print(f"\nreport -> {out}")

    if a.baseline:
        compare(report, json.load(open(a.baseline)))


if __name__ == "__main__":
    main()
