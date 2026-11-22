"""Command line: serve | demo | evaluate | agent {list,submit,approve,revoke}."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import anyio
import yaml

from .config import ROOT, Settings
from .ports import Clock, FakeClock, SystemClock


def _clock() -> Clock:
    fixed = os.environ.get("SWDA_FIXED_NOW")
    return FakeClock(datetime.fromisoformat(fixed)) if fixed else SystemClock()


def cmd_serve(_: argparse.Namespace) -> int:
    from .server import serve_stdio

    anyio.run(serve_stdio)
    return 0


def cmd_demo(_: argparse.Namespace) -> int:
    from .demo import run_demo

    return anyio.run(run_demo)


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .app import build_toolbox
    from .evaluate import evaluate

    report = evaluate(
        build_toolbox(Settings.from_env()), ROOT / "data/eval_requests.jsonl", ROOT / "data/injection_suite.jsonl"
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{report['agent_id']}-{report['agent_version']}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for k, v in report["metrics"].items():
        print(f"  {k:<24} {v:.2f}  (threshold {report['thresholds'][k]:.2f})")
    for f in report["failures"]:
        print(f"  FAIL {f}")
    print(f"{'PASSED' if report['passed'] else 'FAILED'} -> {path}")
    return 0 if report["passed"] else 1


def cmd_agent(args: argparse.Namespace) -> int:
    from .adapters import YamlRegistryStore
    from .approval import ApprovalError, approve, revoke, submit
    from .models import AgentManifest

    store = YamlRegistryStore(Settings.from_env().registry_path)
    try:
        if args.action == "list":
            for a in store.load().agents:
                roles = ",".join(sorted(x.role for x in a.approvals)) or "-"
                print(f"  {a.agent_id:<32} v{a.version:<8} {a.status:<10} expires {a.expires_on}  approvals: {roles}")
        elif args.action == "submit":
            with Path(args.manifest).open(encoding="utf-8") as f:
                manifest = AgentManifest.model_validate(yaml.safe_load(f))
            rec = submit(manifest, store, Path(args.evidence_dir), _clock())
            print(f"Submitted {rec.agent_id} v{rec.version}: all automated checks passed; awaiting human approval")
        elif args.action == "approve":
            rec = approve(args.agent_id, args.version, args.approver, args.role, store, _clock())
            print(f"{rec.agent_id} v{rec.version} is now '{rec.status}' ({len(rec.approvals)}/3 roles)")
        elif args.action == "revoke":
            for rec in revoke(args.agent_id, store):
                print(f"Revoked {rec.agent_id} v{rec.version}; gateways stop serving it within the cache TTL")
    except ApprovalError as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")

    p = argparse.ArgumentParser(
        prog="python -m swda_agent", description="SWDA Service Recommendation Agent over MCP (PoC)"
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the MCP server on stdio").set_defaults(func=cmd_serve)
    sub.add_parser("demo", help="end-to-end demo over MCP").set_defaults(func=cmd_demo)
    ev = sub.add_parser("evaluate", help="run evaluation suites and write approval evidence")
    ev.add_argument("--out", default=str(ROOT / "out" / "evidence"))
    ev.set_defaults(func=cmd_evaluate)

    ag = sub.add_parser("agent", help="registry and approval workflow")
    ag_sub = ag.add_subparsers(dest="action", required=True)
    ag_sub.add_parser("list")
    s = ag_sub.add_parser("submit")
    s.add_argument("manifest")
    s.add_argument("--evidence-dir", default=str(ROOT / "out" / "evidence"))
    a = ag_sub.add_parser("approve")
    a.add_argument("agent_id")
    a.add_argument("version")
    a.add_argument("--as", dest="approver", required=True)
    a.add_argument("--role", required=True, choices=["product_owner", "security", "data_owner"])
    r = ag_sub.add_parser("revoke")
    r.add_argument("agent_id")
    ag.set_defaults(func=cmd_agent)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
