from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest

from btframework.data.models import Bar
from btframework.replay.server import ReplayApp, encode
from btframework.replay.session import ReplaySession, InstrumentProfile


class Detector:
    journal_catalog = {"version": "test_v1", "fields": [{"id": "obv", "options": [{"id": "aligned"}]}]}
    context = {"assessment": {"decision": "reject", "reason": "roc_sin_momentum_al_cruce", "direction": 1}}

    def __call__(self, bars):
        return []

    def journal_context(self, now, direction):
        return self.context


def bars():
    values = [(100,101,99,100),(100,106,99,105),(105,106,104,105),(105,106,104,105)]
    return [Bar(datetime(2025,1,6,15)+timedelta(minutes=i),*[Decimal(v) for v in row],volume=100,symbol="MNQ") for i,row in enumerate(values)]


def adapter(source):
    return [], Detector()


def test_native_outcome_is_unchanged_and_journal_links_to_trade():
    sessions = [ReplaySession(bars(),InstrumentProfile(),provisional_detector=Detector()) for _ in range(2)]
    for i,session in enumerate(sessions):
        session.advance()
        kwargs = {} if i == 0 else {"journal": {"note":"  <script>human</script>  ","criteria":{"obv":"aligned"}}}
        session.place("BUY",1,"MARKET",95,105,**kwargs)
        if i:
            assert session.journal_records[0]["engine_context"]["assessment"]["decision"] == "reject"
            assert session.journal_records[0]["decided_at"] == "2025-01-06T15:01:00"
        session.advance()
    expected,actual = sessions[0].strategy.trades[0],dict(sessions[1].strategy.trades[0])
    assert actual.pop("journal_id") == "journal:1"
    assert actual == expected
    record = sessions[1].journal_snapshot()[0]
    assert record["manual"]["note"] == "<script>human</script>"
    assert record["status"] == "closed" and record["outcome"]["r"] == 1
    assert sessions[1].engine.account.balance == sessions[0].engine.account.balance


@pytest.mark.parametrize("annotation", [{"note":"x"*2001},{"criteria":{"bad":"aligned"}},{"criteria":{"obv":"fake"}},{"criteria":[]},{"note":7},{"engine_context":{}}])
def test_invalid_annotation_never_places_an_order(annotation):
    session=ReplaySession(bars(),InstrumentProfile(),provisional_detector=Detector())
    session.advance()
    with pytest.raises(ValueError,match="invalid_journal"):
        session.place("BUY",1,"MARKET",95,105,journal=annotation)
    assert not session.journal_records and session.strategy.intent is None and not session.engine.pending_orders


def test_cancel_and_empty_annotation_are_recorded_separately():
    session=ReplaySession(bars(),InstrumentProfile(),provisional_detector=Detector())
    session.advance();session.place("BUY",1,"LIMIT",90,105,price=95,journal={})
    assert session.journal_snapshot()[0]["status"] == "pending"
    session.cancel()
    assert session.journal_snapshot()[0]["status"] == "unfilled"
    session.place("BUY",1,"MARKET",95,105,journal={});session.advance()
    assert [r["id"] for r in session.journal_snapshot()] == ["journal:1","journal:2"]
    assert session.journal_snapshot()[1]["manual"] == {"note":"","criteria":{}}


def test_save_restore_keeps_original_engine_snapshot_and_disk_archive(tmp_path):
    app=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
    app.command("create",{});app.command("advance",{})
    app.command("order",{"side":"BUY","quantity":"1","order_type":"MARKET","sl":"95","tp":"105","activate":True,"journal":{"note":"entry","criteria":{"obv":"aligned"}}})
    original=json.loads(json.dumps(app.session.journal_snapshot(),default=encode))
    saved=deepcopy(app.command("save",{}))
    archive=json.loads(app.journal_archive.read_text())
    assert archive["records"] == original
    changed=deepcopy(Detector.context)
    try:
        Detector.context["assessment"]["reason"]="changed_after_entry"
        restored=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
        restored.command("restore",{"session":saved})
        assert json.loads(json.dumps(restored.session.journal_snapshot(),default=encode)) == original
        assert restored.state({})["journal"] == []
        assert restored.command("report",{})["journal"]
        assert restored.command("journal",{})["journal"][0]["manual"]["note"] == "entry"
    finally:
        Detector.context=changed


def test_storage_error_keeps_native_order_and_is_reported(tmp_path, monkeypatch):
    app=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
    app.command("create",{});app.command("advance",{})
    def fail(*args,**kwargs):
        raise OSError("disk unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(Path,"replace",fail)
        result=app.command("order",{"side":"BUY","quantity":"1","order_type":"MARKET","journal":{}})
    assert result["journal_storage_error"] and app.session.strategy.intent
    assert not app.journal_archive.exists()
    app.command("state",{})
    assert app.journal_archive.exists() and not app.journal_storage_error


def test_new_session_does_not_overwrite_previous_journal_archive(tmp_path):
    app=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
    for _ in range(2):
        app.command("create",{});app.command("advance",{})
        app.command("order",{"side":"BUY","quantity":"1","order_type":"MARKET","journal":{}})
    assert len(list(tmp_path.glob("replay-journal-*.json"))) == 2


def test_edit_after_close_preserves_original_annotation_engine_and_native_outcome(tmp_path):
    app=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
    app.command('create',{});app.command('advance',{})
    app.command('order',{'side':'BUY','quantity':'1','order_type':'MARKET','sl':'95','tp':'105','activate':True,'journal':{}})
    original=deepcopy(app.session.journal_records[0]);trade=deepcopy(app.session.strategy.trades[0]);balance=app.session.engine.account.balance
    result=app.command('journal_edit',{'journal_id':'journal:1','journal':{'note':'added after close','criteria':{'obv':'aligned'}},'expected_revision':0})
    row=result['journal'][0]
    assert row['annotation']['note']=='added after close' and row['manual']==original['manual']
    assert row['engine_context']==original['engine_context'] and row['revisions'][0]['after_close']
    assert app.session.strategy.trades[0]==trade and app.session.engine.account.balance==balance
    assert json.loads(app.journal_archive.read_text())['records'][0]['annotation']['note']=='added after close'
    saved=app.command('save',{})
    restored=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
    restored.command('restore',{'session':saved})
    assert restored.session.journal_records==app.session.journal_records
    assert restored.session.strategy.trades==app.session.strategy.trades


def test_legacy_trade_can_be_completed_without_fabricating_entry_evidence(tmp_path):
    app=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
    app.command('create',{});app.command('advance',{})
    app.command('order',{'side':'BUY','quantity':'1','order_type':'MARKET','sl':'95','tp':'105','activate':True})
    placeholder=app.command('journal',{})['journal'][0]
    assert placeholder['id']=='legacy-trade:0' and placeholder['engine_context'] is None
    result=app.command('journal_edit',{'journal_id':placeholder['id'],'journal':{'note':'legacy annotation','criteria':{}},'expected_revision':0})
    assert result['journal'][0]['annotation']['note']=='legacy annotation'
    assert result['journal'][0]['retrospective_only'] and result['journal'][0]['engine_context'] is None
    restored=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
    restored.command('restore',{'session':app.command('save',{})})
    assert restored.session.journal_records==app.session.journal_records
    assert restored.session.strategy.trades[0]['journal_id']=='legacy-trade:0'


def test_edit_revisions_conflicts_and_invalid_notes_leave_record_intact():
    session=ReplaySession(bars(),InstrumentProfile(),provisional_detector=Detector())
    session.advance();session.place('BUY',1,'MARKET',95,105,journal={})
    session.update_journal('journal:1',{'note':'first'},0)
    original=deepcopy(session.journal_records)
    with pytest.raises(ValueError,match='journal_conflict'):
        session.update_journal('journal:1',{'note':'stale'},0)
    with pytest.raises(ValueError,match='invalid_journal'):
        session.update_journal('journal:1',{'note':'x'*2001},1)
    assert original==session.journal_records
    session.update_journal('journal:1',{'note':'second'},1)
    assert [row['manual']['note'] for row in session.journal_records[0]['revisions']]==['first','second']
    assert not session.journal_records[0]['revisions'][0]['after_close']
    assert session.journal_records[0]['manual']['note']==''


def test_autofill_uses_frozen_entry_evidence_and_does_not_edit_journal(tmp_path,monkeypatch):
    def suggest(context,direction,at):
        return {'criteria':{'obv':'aligned'},'values':{'direction':direction},'as_of':at,'assessment':deepcopy(context['assessment'])} if context else {'criteria':{},'values':{},'as_of':None}
    monkeypatch.setattr(Detector,'journal_suggestions',staticmethod(suggest),raising=False)
    app=ReplayApp(bars(),adapter=adapter,checkpoint_dir=tmp_path)
    app.command('create',{});app.command('advance',{})
    preview=app.command('journal_autofill',{'side':'BUY'})
    app.command('order',{'side':'BUY','quantity':'1','order_type':'MARKET','sl':'95','tp':'105','activate':True,'journal':{}})
    original=deepcopy(app.session.journal_records);count=len(app.commands)
    result=app.command('journal_autofill',{'journal_id':'journal:1','side':'SELL'})
    assert result['as_of']==preview['as_of'] and result['values']['direction']==1
    assert app.session.journal_records==original and len(app.commands)==count
