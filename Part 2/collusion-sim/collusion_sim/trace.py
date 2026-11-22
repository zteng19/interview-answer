"""Append-only, hash-chained trace store (point 10).

- One JSONL file per run; each event carries prev_hash and hash.
- A runs manifest chains runs together; manifest writes are serialised with a lock.
- The latest head hash is returned to the caller and printed to the console – it is
  never written next to the traces, so whoever can edit traces cannot quietly fix it.
- An injected clock keeps mock and regression runs deterministic.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:                                    # macOS / Linux
    import fcntl

    def _lock(f):
        fcntl.flock(f, fcntl.LOCK_EX)

    def _unlock(f):
        fcntl.flock(f, fcntl.LOCK_UN)
except ImportError:                     # Windows
    import msvcrt

    def _lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


class FakeClock:
    """Deterministic clock; agents' simulated latency advances it."""

    def __init__(self, start: str = "2026-01-01T00:00:00+00:00"):
        self.t = datetime.fromisoformat(start)

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class RealClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def advance(self, seconds: float) -> None:  # real time advances itself
        pass


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class TraceWriter:
    def __init__(self, out_dir: Path, run_id: str, clock):
        self.dir = Path(out_dir) / "traces"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{run_id}.jsonl"
        self.path.write_text("", encoding="utf-8")
        self.run_id = run_id
        self.clock = clock
        self.seq = 0
        self.prev = "GENESIS"
        self.events: list[dict] = []

    def emit(self, kind: str, **payload) -> int:
        self.seq += 1
        ev = {"seq": self.seq, "ts": self.clock.now().isoformat(), "run_id": self.run_id,
              "kind": kind, "payload": payload, "prev_hash": self.prev}
        ev["hash"] = _h(ev)
        self.prev = ev["hash"]
        self.events.append(ev)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, sort_keys=True, default=str) + "\n")
        return self.seq

    def close(self, final_state: str) -> str:
        """Append this run to the manifest and return the new head hash."""
        manifest = self.dir / "manifest.jsonl"
        with open(manifest, "a+", encoding="utf-8") as f:
            _lock(f)
            f.seek(0)
            lines = [l for l in f.read().splitlines() if l.strip()]
            prev = json.loads(lines[-1])["entry_hash"] if lines else "GENESIS"
            entry = {"run_id": self.run_id, "final_state": final_state,
                     "last_event_hash": self.prev, "events": self.seq, "prev_entry_hash": prev}
            entry["entry_hash"] = _h(entry)
            f.seek(0, 2)
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
            _unlock(f)
        return entry["entry_hash"]


def verify_trace(path: Path) -> tuple[bool, str]:
    prev = "GENESIS"
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        h = ev.pop("hash")
        if ev["prev_hash"] != prev or _h(ev) != h:
            return False, f"chain broken at seq {ev['seq']}"
        prev = h
    return True, prev


def verify_manifest(path: Path, expected_head: str | None = None) -> tuple[bool, str]:
    prev = "GENESIS"
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        h = e.pop("entry_hash")
        if e["prev_entry_hash"] != prev or _h(e) != h:
            return False, f"manifest broken at {e['run_id']}"
        prev = h
    if expected_head and prev != expected_head:
        return False, "head hash mismatch (runs deleted or rewritten)"
    return True, prev