"""Command line interface.

  python -m collusion_sim run --all                 run every scripted scenario
  python -m collusion_sim run --scenario disc_s3
  python -m collusion_sim review list
  python -m collusion_sim review show PC-disc_s3-P1
  python -m collusion_sim review decide PC-disc_s3-P1 --decision approve_for_testing \\
         --rationale "..." --checked S3,MO-04,MO-05 [--as-reviewer alice]
  python -m collusion_sim review interactive [--as-reviewer alice]
  python -m collusion_sim rerun out/rerun_requests/PC-disc_s3-P1.json
  python -m collusion_sim eval                      run harness, write samples/
  python -m collusion_sim verify                    verify trace hash chains
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .approval import ApprovalError, ApprovalService, citations
from .config import load_config
from .evaluation import evaluate, export_samples
from .runner import run_scenario
from .trace import verify_manifest, verify_trace


def _print_result(res):
    r = res["report"]
    print(f"{r.scenario_id:18s} {r.final_state.value:22s} rounds={r.rounds_used} cards={r.card_ids} "
          f"denied={r.counts['denied_tool_calls']} retries={r.counts['retries']} reasons={r.reason_codes}")


def cmd_run(a):
    cfg = load_config()
    ids = [s["id"] for s in cfg.scenarios["scenarios"]] if a.all else [a.scenario]
    head = None
    for sid in ids:
        res = run_scenario(sid, a.out, cfg=cfg)
        _print_result(res)
        head = res["head_hash"]
    print(f"\nTrace manifest head hash (printed only, not stored with traces): {head}")
    print(f"Cards awaiting review: {a.out}/queue · run reports: {a.out}/reports")


def cmd_rerun(a):
    req = json.loads(Path(a.request).read_text())
    res = run_scenario(req["scenario_id"], a.out, parent_pattern_id=req["parent_pattern_id"],
                       reviewer_codes=req["reviewer_codes"], rerun_index=1)
    _print_result(res)


def _show(card):
    print(json.dumps(json.loads(card.model_dump_json(by_alias=True)), indent=2))
    print("\nCitations to open before deciding:", ", ".join(citations(card)))


def cmd_review(a):
    cfg = load_config()
    svc = ApprovalService(Path(a.out), cfg.run["reviewers"]["allowlist"])
    try:
        if a.action == "list":
            for p in svc.queue():
                card, _ = svc.load(p.stem)
                n = len(svc.decisions(p.stem))
                print(f"{card.pattern_id:28s} {card.card_type:13s} novelty={card.novelty} decisions={n}")
        elif a.action == "show":
            _show(svc.load(a.pattern_id)[0])
        elif a.action == "decide":
            d = svc.decide(a.pattern_id, a.decision, a.rationale, [c for c in a.checked.split(",") if c],
                           as_reviewer=a.as_reviewer, reviewer_codes=a.codes.split(",") if a.codes else [])
            print(f"Recorded {d.decision} by {d.reviewer} (weakened_control={d.weakened_control}) "
                  f"bound to card sha256 {d.card_sha256[:16]}…")
        elif a.action == "interactive":
            from .models import PatternCard  # noqa
            for p in svc.queue():
                card, _ = svc.load(p.stem)
                print("\n" + "=" * 70)
                _show(card)
                checked = []
                for c in citations(card):
                    rec = __import__("collusion_sim.sanitiser", fromlist=["SourceStore"]).SourceStore().get(c)
                    print(f"\n[{c}] ({rec.basis if rec else '?'}) {rec.normalised if rec else 'not found'}")
                    if input("Opened and checked this source? [y/N] ").strip().lower() == "y":
                        checked.append(c)
                choice = input("Decision: (a)pprove for testing / (r)eject / (c)hange request / (s)kip: ").strip().lower()
                if choice not in ("a", "r", "c"):
                    continue
                rationale = input("Rationale (mandatory): ")
                decision = {"a": "approve_for_testing", "r": "reject", "c": "request_changes"}[choice]
                d = svc.decide(card.pattern_id, decision, rationale, checked, as_reviewer=a.as_reviewer)
                print(f"Recorded {d.decision} by {d.reviewer} (weakened_control={d.weakened_control})")
    except ApprovalError as e:
        print(f"Refused: {e}")
        sys.exit(2)


def cmd_eval(a):
    report = evaluate(a.out)
    export_samples(a.out)
    Path("samples").mkdir(exist_ok=True)
    Path("samples/eval_report.json").write_text(json.dumps(report, indent=2))
    lines = ["# Evaluation report (mock runs)", "", f"Target: {report['target']}", "",
             "| Scenario | Mode | Final state | Checks |", "|---|---|---|---|"]
    for r in report["scenarios"]:
        n = len(r["checks"]); ok = sum(c["passed"] for c in r["checks"])
        lines.append(f"| {r['scenario']} | {r['mode']} | {r['final_state']} | {ok}/{n} |")
    lines += ["", "## Metrics", ""] + [f"- {k}: {v}" for k, v in report["metrics"].items()]
    failed = [(r["scenario"], c) for r in report["scenarios"] for c in r["checks"] if not c["passed"]]
    if failed:
        lines += ["", "## Failed checks", ""] + [f"- {s}: {c['check']} ({c['detail']})" for s, c in failed]
    Path("samples/eval_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nManifest head hash (printed only): {report['head_hash']}")
    sys.exit(0 if report["passed"] else 1)


def cmd_verify(a):
    ok_all = True
    for p in sorted((Path(a.out) / "traces").glob("*.jsonl")):
        if p.name == "manifest.jsonl":
            continue
        ok, msg = verify_trace(p)
        ok_all &= ok
        print(f"{p.name:40s} {'OK' if ok else 'BROKEN'} {'' if ok else msg}")
    ok, head = verify_manifest(Path(a.out) / "traces" / "manifest.jsonl", a.head)
    print(f"manifest {'OK' if ok else 'BROKEN'} head={head}")
    sys.exit(0 if ok and ok_all else 1)


def main():
    ap = argparse.ArgumentParser(prog="collusion_sim")
    ap.add_argument("--out", default="out")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--scenario"); g.add_argument("--all", action="store_true"); r.set_defaults(fn=cmd_run)
    rr = sub.add_parser("rerun"); rr.add_argument("request"); rr.set_defaults(fn=cmd_rerun)
    rv = sub.add_parser("review"); rv.add_argument("action", choices=["list", "show", "decide", "interactive"])
    rv.add_argument("pattern_id", nargs="?")
    rv.add_argument("--decision", choices=["approve_for_testing", "reject", "request_changes"])
    rv.add_argument("--rationale", default=""); rv.add_argument("--checked", default="")
    rv.add_argument("--as-reviewer", dest="as_reviewer"); rv.add_argument("--codes", default="")
    rv.set_defaults(fn=cmd_review)
    e = sub.add_parser("eval"); e.set_defaults(fn=cmd_eval)
    v = sub.add_parser("verify"); v.add_argument("--head"); v.set_defaults(fn=cmd_verify)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
