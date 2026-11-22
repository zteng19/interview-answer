"""Evaluation harness (point 10).

Under scripted mocks these checks prove our CODE and the end-to-end PLUMBING – they
cannot measure model quality. Each metric names its target (mock vs real model).
Oracle expectations in eval/oracle.yaml were written separately from the mock scripts
(same author, so a separately written oracle, not an independent one).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import ROOT, load_config, load_yaml
from .gate import scan_text
from .runner import run_scenario
from .trace import verify_manifest, verify_trace


def _card_summary(card) -> dict:
    origins: dict[str, int] = {}
    for s in card.statements:
        origins[s.origin] = origins.get(s.origin, 0) + 1
    return {
        "card_type": card.card_type,
        "feasible_subsets": card.feasible_subsets,
        "participants": {p.actor: p.role + ("+victim" if p.victim else "") for p in card.participants},
        "novelty": card.novelty,
        "mo_matches": {m.mo_id: m.relation for m in card.mo_matches},
        "source_support": card.source_support,
        "origins": origins,
        "disclaimer": card.disclaimer,
        "step_origin_fields_ok": all(set(st.origin) == {"action", "text", "preconditions", "effects", "mechanisms"}
                                     for st in card.steps),
    }


def evaluate(out_dir: str | Path = "out", fresh: bool = True) -> dict:
    out = Path(out_dir)
    if fresh and out.exists():
        shutil.rmtree(out)
    cfg = load_config()
    oracle = load_yaml("eval/oracle.yaml")["scenarios"]
    results, heads = [], []
    for sc in cfg.scenarios["scenarios"]:
        res = run_scenario(sc["id"], out, cfg=cfg, built_by="pipeline")
        heads.append(res["head_hash"])
        exp = oracle[sc["id"]]
        checks = []

        def chk(name, ok, detail=""):
            checks.append({"check": name, "passed": bool(ok), "detail": detail})

        chk("final_state", res["final_state"].value == exp["final_state"],
            f"{res['final_state'].value} vs {exp['final_state']}")
        cards = [_card_summary(c) for c in res["cards"]]
        for i, ec in enumerate(exp.get("cards", [])):
            got = cards[i] if i < len(cards) else {}
            for k, v in ec.items():
                chk(f"card[{i}].{k}", got.get(k) == v, f"{got.get(k)} vs {v}")
        for k, v in exp.get("counts_min", {}).items():
            chk(f"counts.{k}>={v}", res["report"].counts.get(k, 0) >= v, str(res["report"].counts.get(k)))
        for k, v in exp.get("counts_eq", {}).items():
            chk(f"counts.{k}=={v}", res["report"].counts.get(k, 0) == v, str(res["report"].counts.get(k)))
        events = [json.loads(l) for l in (out / "traces" / f"{res['report'].run_id}.jsonl").read_text().splitlines()]
        blocked = sorted({e["payload"].get("code") for e in events
                          if e["kind"] in ("tool_call", "message_blocked") and e["payload"].get("code")})
        for code in exp.get("blocked_codes", []):
            chk(f"blocked:{code}", any(b.startswith(code) for b in blocked), ",".join(blocked))
        if exp.get("recipient_filter_applied"):
            chk("recipient_filter_applied", any(e["kind"] == "message_delivered" and e["payload"]["filtered"]
                                                for e in events))
        ok, _ = verify_trace(out / "traces" / f"{res['report'].run_id}.jsonl")
        chk("trace_hash_chain_valid", ok)
        results.append({"scenario": sc["id"], "mode": sc["mode"], "final_state": res["final_state"].value,
                        "cards": cards, "counts": res["report"].counts, "checks": checks,
                        "passed": all(c["passed"] for c in checks)})
    mok, head = verify_manifest(out / "traces" / "manifest.jsonl", heads[-1])
    all_checks = [c for r in results for c in r["checks"]]
    report = {
        "target": "mock runs – plumbing and code paths only; model quality needs a real model",
        "scenarios": results,
        "metrics": {
            "scenarios_passed": f"{sum(r['passed'] for r in results)}/{len(results)}",
            "oracle_checks_passed": f"{sum(c['passed'] for c in all_checks)}/{len(all_checks)}",
            "final_state_accuracy": f"{sum(c['passed'] for c in all_checks if c['check'] == 'final_state')}/{len(results)}",
            "cards_schema_valid": "100% (cards are validated Pydantic models)",
            "manifest_chain_valid": mok,
        },
        "head_hash": head,
        "passed": all(r["passed"] for r in results) and mok,
    }
    return report


def _redact(obj, key="payload"):
    """Recursively pass every free-text string through the gate's rules."""
    if isinstance(obj, dict):
        return {k: _redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v, key) for v in obj]
    if isinstance(obj, str) and key in ("body", "response", "text", "description", "private_reasoning"):
        codes = scan_text(obj, key)
        if codes:
            return f"[redacted by safety gate: {', '.join(codes)}]"
    return obj


def export_samples(out_dir: str | Path = "out", samples_dir: str | Path = "samples") -> None:
    """Copy cards, run reports and REDACTED traces into the repo. Message bodies and agent
    responses go through the gate's rules; anything flagged is replaced."""
    out, samples = Path(out_dir), ROOT / samples_dir
    if samples.exists():
        shutil.rmtree(samples)
    for sub in ("cards", "reports", "traces"):
        (samples / sub).mkdir(parents=True)
    for p in (out / "queue").glob("*.json"):
        shutil.copy(p, samples / "cards" / p.name)
    for p in (out / "reports").glob("*.json"):
        shutil.copy(p, samples / "reports" / p.name)
    for p in (out / "traces").glob("*.jsonl"):
        if p.name == "manifest.jsonl":
            continue
        lines = []
        for line in p.read_text().splitlines():
            ev = json.loads(line)
            ev["payload"] = _redact(ev["payload"])
            lines.append(json.dumps(ev, sort_keys=True))
        (samples / "traces" / p.name).write_text("\n".join(lines) + "\n")
    (samples / "traces" / "README.md").write_text(
        "Sample traces from synthetic mock runs. Message bodies and agent responses were passed through the\n"
        "safety gate's rules; flagged content is redacted. Because of redaction, these copies no longer\n"
        "verify against their hash chain – the originals stay in out/traces (restricted).\n")
