from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import importlib
import io
import json
import logging
import secrets
import threading
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import plotly.offline

from btframework.data.models import Bar
from btframework.replay.persistence import ReplayStore
from btframework.replay.session import InstrumentProfile, ReplaySession, load_csv, load_csv_file

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def encode(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.name
    raise TypeError(type(value).__name__)


class ReplayApp:
    def __init__(self, bars=None, profile=None, adapter=None, base_minutes=1, start=None, end=None, context_bars=None, context_minutes=60, context_source="", checkpoint_dir=None, state_path=None):
        self.bars = bars or []
        self.profile = profile or InstrumentProfile()
        self.adapter = adapter
        self.base_minutes = base_minutes
        self.start, self.end = start, end
        self.context_bars,self.context_minutes,self.context_source=context_bars or [],context_minutes,context_source
        self.session = None
        self.commands = []
        self.definition = None
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else Path(tempfile.gettempdir()) / "replay-checkpoints"
        self.checkpoints = {}
        self.journal_archive = self.checkpoint_dir / ("replay-journal-" + secrets.token_hex(6) + ".json")
        self._journal_signature = None
        self.journal_storage_error = False
        self._restoring = False
        self.persistence_error = False
        self.revision = 0
        self.store = ReplayStore(state_path, encode) if state_path else None
        if self.store:
            document, self.revision, receipt = self.store.load()
            self._load_checkpoints()
            if document:
                self._restore_document(document)
                if receipt and self._recovery_receipt() != receipt:
                    raise ValueError("replay_recovery_mismatch")

    def create(self, args):
        profile = InstrumentProfile(**args.get("profile", asdict(self.profile)))
        bars = self.bars
        if args.get("csv"):
            bars = load_csv_stream(args["csv"], profile.symbol)
        else:
            if profile.symbol != self.profile.symbol:
                raise ValueError("upload_matching_data")
            bars = [Bar(b.timestamp, b.open, b.high, b.low, b.close, b.volume, profile.symbol)
                    for b in bars]
        uses_default_source = bool(args.get("uses_default_source", not args.get("csv")))
        navigation_bars = self.bars if uses_default_source else bars
        start = args.get("start", self.start)
        end = args.get("end", self.end)
        start = datetime.fromisoformat(start) if isinstance(start, str) and start else start or None
        end = datetime.fromisoformat(end) if isinstance(end, str) and end else end or None
        if end:
            bars = [bar for bar in bars if bar.timestamp < end]
        history = [bar for bar in bars if start and bar.timestamp < start]
        bars = [bar for bar in bars if not start or bar.timestamp >= start]
        minutes = int(args.get("base_minutes", self.base_minutes))
        strategy = args.get("strategy", "mnq_m5" if self.adapter and profile.symbol == "MNQ" else "manual")
        if strategy != "manual" and (self.adapter is None or profile.symbol != "MNQ" or minutes != 1):
            raise ValueError("strategy_unavailable")
        events, detector = self.adapter(history + bars) if strategy != "manual" else ([], None)
        session = ReplaySession(bars, profile, args.get("capital", 10000), minutes,
                                events, strategy, detector, history_bars=history)
        context = load_csv_stream(args["context_csv"],profile.symbol) if args.get("context_csv") else self.context_bars if not args.get("csv") and profile.symbol==self.profile.symbol else []
        if context:
            session.set_context(context,int(args.get("context_minutes",self.context_minutes)),args.get("context_source",self.context_source))
        self._journal_signature = None
        self.journal_archive = self.checkpoint_dir / ("replay-journal-" + secrets.token_hex(6) + ".json")
        self.session = session
        self._navigation_bars = navigation_bars
        self.definition = {"uses_default_source": uses_default_source, "profile": asdict(profile), "capital": str(session.capital),
                           "base_minutes": minutes, "strategy": strategy,
                           "csv": csv_text(history + bars),
                           "start": start.isoformat() if start else None,
                           "end": end.isoformat() if end else None,
                           "context_csv": csv_text(context) if context else None,
                           "context_minutes": session.context_minutes, "context_source": session.context_source}
        self.commands = []
        return self.state(args)

    def state(self, args):
        if self._restoring:
            return {}
        if not self.session:
            return {"ready": False, "profile": asdict(self.profile), "has_data": bool(self.bars),
                    "strategies": ["manual", "mnq_m5"] if self.adapter else ["manual"],
                    "start": self.start.isoformat() if self.start else "",
                    "end": self.end.isoformat() if self.end else "", "revision": self.revision,
                    "persistence": "blocked" if self.persistence_error else "saved" if self.store else "off"}
        self.session._quiet = False
        left, right = max(self.session.base_minutes, int(args.get("left", 5))), max(self.session.base_minutes, int(args.get("right", 1)))
        limit = max(100, min(int(args.get("history_limit", 2000)), 10000))
        before = args.get("view_before")
        if before is not None:
            before = int(before)
            if before > int(self.session.now.replace(tzinfo=timezone.utc).timestamp()):
                raise ValueError("futureView")
        charts = [self.session.chart(left, limit, before), self.session.chart(right, limit, before)]
        return {"ready": True, **self.session.snapshot(compact=bool(args.get("compact")), include_journal=False),
                "strategies": ["manual", "mnq_m5"] if self.adapter else ["manual"],
                "source_bounds": [self._navigation_bars[0].timestamp, self._navigation_bars[-1].timestamp] if self._navigation_bars else [],
                "checkpoints": [{"id": k, "label": v["label"]} for k, v in self.checkpoints.items()],
                "revision": self.revision,
                "persistence": "blocked" if self.persistence_error else "saved" if self.store else "off",
                "charts": charts, "visuals": [self.session.chart_visuals(tf, rows, datetime.fromtimestamp(before, timezone.utc).replace(tzinfo=None) if before is not None else None) for tf, rows in zip((left, right), charts)]}

    def command(self, action, args):
        read_only = {"state", "report", "save", "journal", "journal_autofill"}
        if self.persistence_error:
            raise ValueError("persistence_blocked")
        if self.store and action not in read_only:
            expected = args.get("expected_revision")
            if expected is None or int(expected) != self.revision:
                raise ValueError("stale_session")
            if self.session and action in {"create", "restore"}:
                confirmation = "confirm_replace_session" if action == "restore" else "confirm_new_session"
                if args.get(confirmation) is not True:
                    raise ValueError(confirmation)
            args = {key: value for key, value in args.items() if key != "expected_revision"}
        session = self.session
        previous = getattr(session, "_quiet", False) if session else False
        if session and action not in {"state", "report", "save"}:
            session._quiet = True
        try:
            if self.store and self.session and action in {"create", "restore"}:
                self._archive_session(self._session_document())
            result = self._command(action, args)
            if self.store and action not in read_only and not self._restoring:
                try:
                    self.revision = self.store.save(self, action, args)
                except Exception:
                    self.persistence_error = True
                    logger.exception("Replay state could not be committed to durable storage")
                    try:
                        document, self.revision, _ = self.store.load()
                        if document:
                            self._restore_document(document)
                    except Exception:
                        logger.exception("Could not recover the last committed replay state")
                    raise ValueError("persistence_failed")
                if isinstance(result, dict):
                    result["revision"] = self.revision
                    result["persistence"] = "saved"
            if self.session and self.session.journal_records and not self._restoring:
                self._persist_journal()
                result["journal_storage_error"] = self.journal_storage_error
            return result
        finally:
            if session:
                session._quiet = previous

    def _load_checkpoints(self):
        for path in sorted(self.checkpoint_dir.glob("replay-checkpoint-*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                key = path.stem.removeprefix("replay-checkpoint-")
                self.checkpoints[key] = {"label": f"{key} · {len(document.get('commands', []))} acciones", "document": document}
            except (OSError, ValueError, KeyError, TypeError):
                logger.exception("Could not load replay checkpoint %s", path)

    def _restore_document(self, document):
        candidate = ReplayApp(self.bars, self.profile, self.adapter, self.base_minutes,
                              self.start, self.end, self.context_bars, self.context_minutes,
                              self.context_source, self.checkpoint_dir)
        candidate._replay_document(document)
        self.session = candidate.session
        self.commands = candidate.commands
        self.definition = candidate.definition
        self._navigation_bars = candidate._navigation_bars
        self._journal_signature = candidate._journal_signature
        self.journal_archive = candidate.journal_archive

    def _replay_document(self, document):
        if document.get("version") != 1:
            raise ValueError("invalid_session")
        self._restoring = True
        try:
            self.create(document["definition"])
            self.session._quiet = True
            for command in document["commands"]:
                if command["action"] not in {"advance", "order", "modify", "modify_pending", "cancel", "close", "select", "pause", "play", "history", "context", "journal_edit"}:
                    raise ValueError("invalid_session")
                self._command(command["action"], deepcopy(command["args"]))
        finally:
            self._restoring = False
            if self.session:
                self.session._quiet = False
        self.session.playing = False
        if "journal_records" in document:
            original = document["journal_records"]
            generated = self.session.journal_records
            if not isinstance(original, list) or len(original) != len(generated):
                raise ValueError("invalid_journal")
            for stored, replayed in zip(original, generated):
                if any(stored.get(key) != replayed.get(key) for key in ("id", "decided_at", "side", "quantity", "planned_entry", "manual")):
                    raise ValueError("invalid_journal")
            json.dumps(original, default=encode, allow_nan=False)
            self.session.journal_records = deepcopy(original)
        return self.state({})

    def _session_document(self):
        return {"version": 1, "definition": self.definition, "commands": list(self.commands),
                "journal_records": deepcopy(self.session.journal_records)}

    def _recovery_receipt(self):
        orders = []
        for item in self.session.engine.pending_orders:
            order = asdict(item)
            order.pop("id", None)
            order.pop("order_id", None)
            order.pop("created_at", None)
            orders.append(order)
        receipt = {
            "cursor": self.session.cursor,
            "now": self.session.now.isoformat(),
            "account": self.session.engine.account.snapshot(),
            "position": self.session.strategy.active,
            "intent": self.session.strategy.intent,
            "pending": orders,
            "trades": self.session.strategy.trades,
            "scoreboard": self.session.scoreboard(),
            "journal": self.session.journal_records,
            "selected": sorted(self.session.selected),
            "decisions": self.session.decisions,
        }
        serialized = json.dumps(receipt, default=encode, ensure_ascii=True, allow_nan=True,
                                sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()

    def _archive_session(self, document):
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(6)
        path = self.checkpoint_dir / ("replay-checkpoint-" + key + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, default=encode, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
        label = self.session.now.isoformat(timespec="minutes") + " · " + str(len(self.session.strategy.trades)) + " trades"
        self.checkpoints[key] = {"label": label, "document": document}

    def _persist_journal(self):
        session = self.session
        signature = (len(session.journal_records), sum(len(record.get("revisions", [])) for record in session.journal_records), len(session.strategy.trades),
                     (session.strategy.active or {}).get("journal_id"),
                     (session.strategy.intent or {}).get("journal_id"))
        if signature == self._journal_signature:
            return
        temporary = self.journal_archive.with_suffix(".tmp")
        try:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            content = {"version": 1, "profile": asdict(session.profile), "records": session.journal_snapshot()}
            temporary.write_text(json.dumps(content, default=encode, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            temporary.replace(self.journal_archive)
            self._journal_signature = signature
            self.journal_storage_error = False
        except (OSError, ValueError, TypeError):
            self.journal_storage_error = True
            logger.exception("Could not persist replay journal")

    def _command(self, action, args):
        if action == "create":
            return self.create(args)
        if action == "restore":
            self._restore_document(args["session"])
            return self.state(args)
        if action == "state":
            return self.state(args)
        if action in {"navigate", "resume"}:
            if not self.session:
                raise ValueError("no_session")
            target = None
            if action == "resume":
                target = self.checkpoints[args["id"]]["document"]
            else:
                if args.get("confirm_new_session") is not True:
                    raise ValueError("confirm_new_session")
                date = datetime.fromisoformat(args["date"])
                if not self._navigation_bars or not self._navigation_bars[0].timestamp <= date <= self._navigation_bars[-1].timestamp:
                    raise ValueError("date_outside_data")
            document = self._session_document()
            self._archive_session(document)
            if target is not None:
                return self._command("restore", {**args, "session": target})
            excluded = {"start", "end"}
            if self.definition["uses_default_source"]:
                excluded.add("csv")
            definition = {k: v for k, v in self.definition.items() if k not in excluded}
            definition.update(start=date.isoformat(), end=None)
            self.create(definition)
            self.session.advance()
            self.commands.append({"action": "advance", "args": {"steps": 1}})
            return self.state(args)
        if self.session is None:
            raise ValueError("no_session")
        session = self.session
        if action == "context":
            session.set_context(load_csv_stream(args["csv"],session.profile.symbol),int(args["minutes"]),args.get("source",""))
        elif action == "history":
            history = load_csv_stream(args["csv"], session.profile.symbol)
            events, detector = self.adapter(history + session.bars) if session.strategy_name != "manual" else ([], None)
            session.add_history(history, events, detector)
        elif action == "advance":
            session.advance(args.get("steps", 1), args.get("until_event", False))
        elif action == "order":
            session.place(**{k: v for k, v in args.items() if k in {
                "side", "quantity", "order_type", "sl", "tp", "price", "event_id", "journal"}})
            if args.get("activate") and args.get("order_type") == "MARKET":
                session.playing = False
                session.advance()
        elif action == "modify":
            session.modify(**{k: v for k, v in args.items() if k in {"sl", "tp", "trailing", "break_even"}})
        elif action == "modify_pending":
            session.modify_pending(**{k: v for k, v in args.items() if k in {"price", "sl", "tp"}})
        elif action == "cancel":
            session.cancel()
        elif action == "close":
            if args.get("advance") and session.cursor >= len(session.bars):
                raise ValueError("end_close_unavailable")
            session.close()
            if args.get("advance"):
                session.playing = False
                session.advance()
        elif action == "select":
            session.selected = set(args["events"])
        elif action == "play":
            session.playing = True
        elif action == "pause":
            session.playing = False
            if session.stop_reason == "playing":
                session.stop_reason = "paused"
        elif action == "state":
            return self.state(args)
        elif action == "save":
            return self._session_document()
        elif action == "journal_autofill":
            suggest = getattr(session.detector, "journal_suggestions", None)
            capture = getattr(session.detector, "journal_context", None)
            if args.get("journal_id"):
                record = next((record for record in session.journal_snapshot() if record["id"] == args["journal_id"]), None)
                if record is None:
                    raise ValueError("journal_not_found")
                context, side, at = record.get("engine_context"), record["side"], record["decided_at"]
            else:
                if args.get("view_before") is not None:
                    raise ValueError("historicalView")
                side, at = args.get("side"), session.now.isoformat()
                if side not in {"BUY", "SELL"}:
                    raise ValueError("invalid_order")
                context = capture(session.now, 1 if side == "BUY" else -1) if capture else None
            return suggest(context, 1 if side == "BUY" else -1, at) if suggest else {"criteria": {}, "values": {}, "as_of": None}
        elif action == "journal_edit":
            stamp = session.update_journal(args.get("journal_id"), args.get("journal"), args.get("expected_journal_revision", args.get("expected_revision")),
                                           args.get("edited_at") if self._restoring else None)
            args["edited_at"] = stamp
            self.commands.append({"action": action, "args": deepcopy(args)})
            return {"journal": session.journal_snapshot(), "journal_count": len(session.journal_records)}
        elif action == "journal":
            return {"journal": session.journal_snapshot(), "journal_count": len(session.journal_records)}
        elif action == "report":
            return session.snapshot()
        else:
            raise ValueError("invalid_action")
        self.commands.append({"action": action, "args": args})
        return self.state(args)


def csv_text(bars):
    return "timestamp,open,high,low,close,volume\n" + "\n".join(
        f"{b.timestamp.isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume}" for b in bars)


def load_csv_stream(value, symbol):
    return load_csv_file(io.StringIO(value), symbol)


def serve(app, port=8050):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logger.info(format, *args)

        def send(self, status, body, content_type="application/json"):
            if content_type == "application/json":
                body = json.dumps(body, default=encode, allow_nan=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                return self.send(403, {"error": "forbidden"})
            if path == "/":
                self.send(200, (STATIC / "index.html").read_text().replace("__TOKEN__", app.token), "text/html; charset=utf-8")
            elif path == "/plotly.js":
                self.send(200, plotly.offline.get_plotlyjs(), "text/javascript")
            elif path in {"/app.js", "/i18n.js", "/tools.js", "/ticket.js", "/journal.js", "/style.css"}:
                self.send(200, (STATIC / path[1:]).read_bytes(),
                          "text/javascript" if path.endswith("js") else "text/css")
            else:
                self.send(404, {"error": "not_found"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (self.path != "/api" or self.headers.get("X-Replay-Token") != app.token
                    or self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}
                    or origin not in {None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"}):
                return self.send(403, {"error": "forbidden"})
            try:
                size = int(self.headers.get("Content-Length", 0))
                if size < 1 or size > 100_000_000:
                    raise ValueError("invalid_upload")
                body = json.loads(self.rfile.read(size))
                with app.lock:
                    result = app.command(body["action"], body.get("args", {}))
                self.send(200, result)
            except (ValueError, KeyError, TypeError, ArithmeticError) as error:
                self.send(400, {"error": str(error)})
            except Exception:
                logger.exception("Replay request failed")
                self.send(500, {"error": "internal_error"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    logger.info("Replay dashboard: http://127.0.0.1:%s", port)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Historical trading replay dashboard")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--adapter", help="Trusted Python module:function providing strategy events")
    parser.add_argument("--base-minutes", type=int, default=1)
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".quantira" / "replay")
    args = parser.parse_args()
    profile = InstrumentProfile(**json.loads(args.profile.read_text())) if args.profile else InstrumentProfile()
    adapter = None
    if args.adapter:
        module, attribute = args.adapter.split(":", 1)
        adapter = getattr(importlib.import_module(module), attribute)
    bars = load_csv(args.data, profile.symbol) if args.data else []
    logging.basicConfig(level=logging.INFO)
    state_path = args.state_dir / f"replay-{args.port}.sqlite3"
    serve(ReplayApp(bars, profile, adapter, args.base_minutes,
                    checkpoint_dir=args.state_dir / f"checkpoints-{args.port}",
                    state_path=state_path), args.port)


if __name__ == "__main__":
    main()
