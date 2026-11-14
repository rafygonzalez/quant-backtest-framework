"""Entry evidence, versioned annotations and native execution outcomes."""
from copy import deepcopy


def validate_annotation(payload, catalog):
    if not isinstance(payload, dict) or set(payload) - {"note", "criteria"}:
        raise ValueError("invalid_journal")
    note, criteria = payload.get("note", ""), payload.get("criteria", {})
    if not isinstance(note, str) or len(note) > 2000 or not isinstance(criteria, dict):
        raise ValueError("invalid_journal")
    allowed = {field["id"]: {option["id"] for option in field["options"]} for field in catalog["fields"]}
    if any(key not in allowed or not isinstance(value, str) or value not in allowed[key]
           for key, value in criteria.items()):
        raise ValueError("invalid_journal")
    return {"note": note.strip(), "criteria": dict(criteria)}


def legacy_record(index, trade, catalog):
    opened = trade["opened_at"]
    return {"id": "legacy-trade:" + str(index), "version": 1, "catalog": deepcopy(catalog),
            "manual": {"note": "", "criteria": {}}, "engine_context": None,
            "decided_at": opened.isoformat() if hasattr(opened, "isoformat") else opened,
            "decision_time_source": "fill_time_only", "retrospective_only": True,
            "symbol": trade["symbol"], "side": "BUY" if trade["side"] > 0 else "SELL",
            "quantity": str(trade["quantity"]), "planned_entry": None,
            "initial_sl": None, "initial_tp": None}


def journal_rows(records, trades, active, intent, catalog):
    records = list(records)
    closed = {}
    for index, trade in enumerate(trades):
        identity = trade.get("journal_id")
        if not identity:
            record = legacy_record(index, trade, catalog)
            identity = record["id"]
            records.append(record)
        closed[identity] = trade
    rows = []
    for record in records:
        identity = record["id"]
        trade = closed.get(identity)
        status = ("closed" if trade else "open" if active and active.get("journal_id") == identity
                  else "pending" if intent and intent.get("journal_id") == identity else "unfilled")
        revisions = record.get("revisions", [])
        rows.append({**deepcopy(record), "status": status,
                     "annotation": deepcopy(revisions[-1]["manual"] if revisions else record["manual"]),
                     "outcome": {key: trade[key] for key in ("pnl", "r", "opened_at", "closed_at", "entry", "exit", "risk", "commission")} if trade else None})
    return rows
