from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json

import pytest

from btframework.core.engine import BacktestEngine
from btframework.replay.indicators import ChartIndicators
from btframework.data.models import Bar
from btframework.replay.session import InstrumentProfile, ReplaySession, StrategyEvent
from btframework.replay.server import ReplayApp, encode, csv_text


def bars(rows):
    start = datetime(2025, 1, 6, 15)
    return [Bar(start+timedelta(minutes=i), *map(D, map(str, row)), symbol="MNQ", volume=100)
            for i, row in enumerate(rows)]


def session(rows, events=(), capital=10000, **profile):
    return ReplaySession(bars(rows), InstrumentProfile(**profile), capital, events=events)


def test_market_bracket_same_bar_and_costs():
    s = session([(100,101,99,100),(100,106,99,105),(105,106,104,105)], commission="1")
    s.advance()
    s.place("BUY",1,"MARKET",95,105)
    s.advance()
    assert s.strategy.active is None
    assert s.strategy.trades[0]["pnl"] == D(8)
    assert s.strategy.trades[0]["r"] == D("0.8")
    assert s.engine.account.balance == D(10008)


def test_limit_cannot_capture_pre_fill_target():
    s=session([(100,101,99,100),(100,110,94,99),(99,106,98,105)])
    s.advance(); s.place("BUY",1,"LIMIT",90,105,price=95); s.advance()
    assert s.strategy.active is not None
    s.advance()
    assert s.strategy.trades[0]["exit"] == D(105)


def test_closed_bar_never_fills_new_order():
    s=session([(100,110,90,100),(100,101,99,100),(100,101,99,100)])
    s.advance(); s.place("BUY",1,"LIMIT",90,105,price=95)
    assert not s.engine.account.open_positions
    s.advance()
    assert not s.engine.account.open_positions


def test_contracts_margin_and_gap_margin():
    s=session([(100,101,99,100),(100,101,99,100)],capital=10)
    s.advance()
    with pytest.raises(Exception,match="margin"):
        s.place("BUY",1,"MARKET",95,105)
    with pytest.raises(ValueError,match="quantity"):
        s.place("BUY",.5,"MARKET",95,105)
    s=session([(100,101,99,100),(100,101,99,100)], capital=1000, commission="1")
    s.advance();s.place("BUY",1,"MARKET",95,105);s.advance()
    assert not s.engine.account.open_positions
    assert s.engine.account.balance==1000


def test_pause_events_suppressed_while_position_open():
    data=bars([(100,101,99,100),(100,101,99,100),(100,102,99,101),(101,106,100,105),(105,106,104,105)])
    es=[StrategyEvent(str(i),"cross",data[i].timestamp+timedelta(minutes=1),1,100) for i in [0,1,2,4]]
    s=ReplaySession(data,InstrumentProfile(),events=es)
    s.advance(100,True)
    assert s.cursor==1
    s.place("BUY",1,"MARKET",95,105)
    s.advance(100,True)
    assert s.cursor==4 and s.stop_reason=="trade_closed"
    assert s.decisions['1']==s.decisions['2']=='occupied'
    s.advance(100,True)
    assert s.cursor==5 and s.current_events[0].event_id=='4'


def test_partial_chart_hides_future_and_timeframes_sync():
    s=session([(100,101,99,100),(100,120,98,110),(110,150,90,140)])
    s.advance()
    assert s.chart(5)[0]["high"]==101
    s.advance()
    assert s.chart(5)[0]["high"]==120
    assert s.chart(5)[0]["close"]==s.chart(1)[-1]["close"]


def test_modify_be_trailing_and_next_bar():
    s=session([(100,101,99,100),(100,106,99,105),(105,109,104,108),(108,109,105,106),(106,107,105,106)])
    s.advance();s.place("BUY",1,"MARKET",95,120);s.advance()
    s.modify(break_even=True,trailing=2)
    assert s.strategy.active['sl']==100
    s.advance()
    assert s.strategy.active['sl']==106
    s.advance()
    assert s.strategy.active is None
    assert s.strategy.trades[0]['exit']==106


def test_forex_lots_and_spread():
    s=session([(100,101,99,100),(100,101,99,100),(100,101,99,100)],kind="forex", minimum="0.01",step="0.01",contract_size="100000",tick_size="0.01",leverage="100",spread="0.02")
    s.advance();s.place("BUY","0.01","MARKET",95,105);s.advance()
    assert s.strategy.active['entry']==D('100.01')
    s.close();s.advance()
    assert s.strategy.trades[0]['pnl']==D('-20')


def test_save_restore_deterministic():
    data=bars([(100,101,99,100),(100,106,99,105),(105,106,104,105)])
    app=ReplayApp(data)
    app.command('create',{})
    app.command('advance',{})
    app.command('order',dict(side='BUY',quantity=1,order_type='MARKET',sl=95,tp=105))
    app.command('advance',{})
    saved=json.loads(json.dumps(app.command('save',{}),default=encode))
    expected=app.session.snapshot()
    restored=ReplayApp()
    restored.command('restore',{'session':saved})
    assert restored.session.strategy.trades==app.session.strategy.trades
    assert restored.session.snapshot()['scoreboard']==expected['scoreboard']
    json.dumps(restored.state({}),default=encode,allow_nan=False)


def test_step_and_run_same_engine_result():
    data=bars([(100,101,99,100),(100,106,99,105)])
    s=ReplaySession(data,InstrumentProfile())
    s.advance();s.advance()
    stepped=s.engine.result().equity_curve
    result=s.engine.run()
    assert stepped==result.equity_curve


def test_pending_amendments_validate_before_mutating():
    s=session([(100,101,99,100),(100,101,96,100),(100,101,94,100)])
    s.advance();s.place('BUY',1,'LIMIT',90,110,price=95)
    with pytest.raises(ValueError):
        s.modify_pending(price=105)
    assert s.strategy.intent['price']==95
    s.modify_pending(price=97,sl=92,tp=108)
    s.advance()
    assert s.strategy.active['entry']==97
    assert s.strategy.active['sl']==92


def test_stop_short_gap_and_oco():
    s=session([(100,101,99,100),(94,96,90,92),(92,93,91,92)])
    s.advance();s.place('SELL',1,'STOP',105,90,price=95);s.advance()
    assert s.strategy.active is None
    assert s.strategy.trades[0]['entry']==94
    assert s.strategy.trades[0]['exit']==90
    assert not s.engine.pending_orders


def test_gap_past_bracket_rejects_without_account_mutation():
    s=session([(100,101,99,100),(110,112,109,111),(111,112,110,111)])
    s.advance();s.place('BUY',1,'MARKET',95,105);s.advance()
    assert not s.engine.account.open_positions
    assert s.engine.account.balance==10000
    assert not s.engine.pending_orders


def test_restore_open_trade_after_stop_amendment():
    data=bars([(100,101,99,100),(100,105,99,104),(104,106,103,105),(105,121,104,120)])
    app=ReplayApp(data)
    for action,args in [('create',{}),('advance',{}),('order',dict(side='BUY',quantity=1,order_type='MARKET',sl=95,tp=120)),('advance',{}),('modify',{'break_even':True,'trailing':2})]:
        app.command(action,args)
    saved=json.loads(json.dumps(app.command('save',{}),default=encode))
    restored=ReplayApp();restored.command('restore',{'session':saved})
    assert restored.session.strategy.active==app.session.strategy.active
    app.command('advance',{'steps':100,'until_event':True})
    restored.command('advance',{'steps':100,'until_event':True})
    assert restored.session.strategy.trades==app.session.strategy.trades


def test_empty_server_state_is_available():
    state=ReplayApp().command('state',{})
    assert state['ready'] is False
    assert state['strategies']==['manual']


def test_no_future_event_exposed_at_cursor():
    data=bars([(100,101,99,100)]*5)
    event=StrategyEvent('future','cross',data[-1].timestamp+timedelta(minutes=1),1,100)
    s=ReplaySession(data,InstrumentProfile(),events=[event])
    s.advance()
    assert s.snapshot()['markers']==[]
    assert s.snapshot()['events']==[]
    assert s.scoreboard()['opportunities']=={}


def test_warmup_visible_at_start_without_execution_or_future_prices():
    data=bars([(100+i,101+i,99+i,100+i) for i in range(200)])
    app=ReplayApp(data,start=data[150].timestamp)
    state=app.command('create',{'strategy':'manual'})
    assert state['cursor']==0 and state['history_bars']==150
    assert state['account']['balance']==10000
    assert state['equity_curve']==[] and state['trades']==[]
    chart=state['charts'][0]
    assert len(chart)==30 and chart[-1]['complete']
    assert chart[-1]['close']==249
    assert chart[-1]['ma20']==sum(100+i for i in range(54,150,5))/20
    app.command('advance',{})
    assert app.session.chart(5)[-1]['high']==251
    assert app.session.chart(5)[-1]['close']==250
    assert app.session.chart(5)[-1]['complete'] is False


def test_incomplete_closed_m5_does_not_enter_ma():
    data=bars([(100+i,101+i,99+i,100+i) for i in range(150)])
    data.pop(7)
    s=ReplaySession(data[100:],InstrumentProfile(),history_bars=data[:100])
    chart=s.chart(5)
    start=int(datetime(2025,1,6,15).timestamp())
    assert all(row['time']!=start+300 for row in chart)
    assert all(row['complete'] for row in chart[:-1])


def test_add_history_preserves_pending_orders_account_and_decisions():
    data=bars([(100,101,99,100)]*120)
    s=ReplaySession(data[100:],InstrumentProfile())
    s.advance();s.place('BUY',1,'LIMIT',90,110,price=95)
    before=s.snapshot()
    s.decisions['existing']='skipped'
    future=StrategyEvent('future','cross',data[110].timestamp+timedelta(minutes=1),1,100)
    s.add_history(data[:100],[future])
    after=s.snapshot()
    for key in ('cursor','now','account','pending','intent','trades','equity_curve'):
        assert after[key]==before[key]
    assert s.decisions=={'existing':'skipped'}
    assert s.chart(5)[-1]['ma20']==100
    s.selected.add('cross')
    s.advance(100,True)
    assert s.current_events[0].event_id=='future'


def test_history_session_and_mid_session_upgrade_restore_exactly():
    data=bars([(100,101,99,100)]*180)
    app=ReplayApp(data[150:])
    app.command('create',{})
    app.command('advance',{})
    app.command('order',dict(side='BUY',quantity=1,order_type='LIMIT',sl=90,tp=110,price=95))
    app.command('history',{'csv':csv_text(data[:150])})
    saved=json.loads(json.dumps(app.command('save',{}),default=encode))
    restored=ReplayApp()
    state=restored.command('restore',{'session':saved})
    assert state['history_bars']==150
    assert restored.session.chart(5)==app.session.chart(5)
    assert restored.session.strategy.intent==app.session.strategy.intent
    assert restored.session.engine.account.balance==10000
    assert len(restored.session.engine.pending_orders)==1


def test_warmup_events_do_not_count_as_opportunities():
    data=bars([(100,101,99,100)]*180)
    events=[StrategyEvent(str(i),'cross',data[i].timestamp+timedelta(minutes=1),1,100) for i in (50,100,160)]
    s=ReplaySession(data[150:],InstrumentProfile(),events=events,history_bars=data[:150])
    assert s.decisions=={} and [e.event_id for e in s.seen]==['50','100']
    s.advance(100,True)
    assert s.current_events[0].event_id=='160'
    assert s.decisions=={}


def test_add_history_does_not_change_open_position_or_its_resolution():
    data=bars([(100,101,99,100)]*100+[(100,101,99,100),(100,105,99,104),(104,111,103,110),(110,111,109,110)])
    left=ReplaySession(data[100:],InstrumentProfile())
    right=ReplaySession(data[100:],InstrumentProfile())
    for session in (left,right):
        session.advance();session.place('BUY',1,'MARKET',95,110);session.advance()
    before=right.snapshot()
    right.add_history(data[:100],[])
    after=right.snapshot()
    for key in ('cursor','now','account','position','intent','pending','decisions','equity_curve'):
        assert after[key]==before[key]
    left.advance();right.advance()
    assert left.strategy.trades==right.strategy.trades
    assert left.engine.account.balance==right.engine.account.balance


def test_hourly_context_expands_h4_without_future_or_execution_changes():
    data=bars([(100,101,99,100)]*10)
    s=ReplaySession(data,InstrumentProfile())
    s.advance()
    before=s.snapshot()
    context=[Bar(data[0].timestamp-timedelta(hours=200-i),D(100),D(101),D(99),D(100),volume=100,symbol='MNQ') for i in range(200)]
    context.append(Bar(data[0].timestamp+timedelta(hours=1),D(100),D(999999),D(99),D(100),volume=100,symbol='MNQ'))
    s.set_context(context,60,'MNQU5 native H1')
    assert len(s.chart(60))>=200
    assert len(s.chart(240))>=49
    assert all(row['high']==101 for row in s.chart(240))
    assert s.snapshot()['account']==before['account']
    assert s.engine.result().equity_curve==before['equity_curve']


def test_indicator_preview_does_not_mutate_closed_history():
    engine=ChartIndicators()
    for i in range(50):result=engine.update(dict(open=100+i,high=101+i,low=99+i,close=100+i,volume=10))
    preview=engine.preview(dict(open=150,high=200,low=149,close=190,volume=999))
    assert preview['roc9']>result['roc9']
    after=engine.update(dict(open=150,high=151,low=149,close=150,volume=10))
    assert after['obv']==500
    assert after['roc9']==100*(150-141)/141
    assert after['obv_flow']==1


def test_context_session_export_retains_native_history_and_replay_state():
    data=bars([(100,101,99,100)]*10)
    context=[Bar(data[0].timestamp-timedelta(hours=100-i),D(100),D(101),D(99),D(100),volume=100,symbol='MNQ') for i in range(100)]
    app=ReplayApp(data,context_bars=context,context_source='MNQU5 H1')
    app.command('create',{})
    app.command('advance',{})
    saved=json.loads(json.dumps(app.command('save',{}),default=encode))
    restored=ReplayApp()
    state=restored.command('restore',{'session':saved})
    assert state['context_source']=='MNQU5 H1'
    assert restored.session.chart(60)==app.session.chart(60)
    assert state['account']==app.session.snapshot()['account']


def test_uploaded_data_does_not_inherit_another_contract_context():
    data=bars([(100,101,99,100)]*10)
    app=ReplayApp(data,context_bars=data,context_source='MNQU5 H1')
    app.command('create',{'csv':csv_text(data),'strategy':'manual'})
    assert app.session.context_bars==[]


def test_market_activation_and_close_use_following_native_bar():
    data = bars([(100,101,99,100),(102,103,101,102),(104,105,103,104),(106,107,105,106)])
    app = ReplayApp(data)
    app.command('create', {'strategy':'manual'})
    app.command('advance', {})
    opened = app.command('order', {'side':'BUY','quantity':1,'order_type':'MARKET',
                                  'sl':90,'tp':120,'activate':True})
    assert opened['cursor'] == 2
    assert opened['position']['entry'] == D(102)
    closed = app.command('close', {'advance':True})
    assert closed['cursor'] == 3 and closed['position'] is None
    assert closed['trades'][0]['exit'] == D(104)
    restored = ReplayApp(data).command('restore', {'session':app.command('save', {})})
    assert restored['trades'] == closed['trades']
    assert restored['account'] == closed['account']


def test_end_close_preserves_protective_orders():
    data = bars([(100,101,99,100),(102,103,101,102)])
    app = ReplayApp(data)
    app.command('create', {'strategy':'manual'})
    app.command('advance', {})
    app.command('order', {'side':'BUY','quantity':1,'order_type':'MARKET','sl':90,'tp':120,'activate':True})
    pending = list(app.session.engine.pending_orders)
    with pytest.raises(ValueError, match='end_close_unavailable'):
        app.command('close', {'advance':True})
    assert app.session.engine.pending_orders == pending
    assert app.session.strategy.active


def test_date_navigation_checkpoint_preserves_full_trading_state(tmp_path, monkeypatch):
    monkeypatch.setattr('btframework.replay.server.tempfile.gettempdir', lambda:str(tmp_path))
    data = bars([(100,101,99,100)] * 20)
    app = ReplayApp(data, checkpoint_dir=tmp_path)
    app.command('create', {'strategy':'manual'})
    app.command('advance', {})
    before = app.command('order', {'side':'BUY','quantity':1,'order_type':'LIMIT','price':99,'sl':90,'tp':120})
    navigated = app.command('navigate', {'date':data[10].timestamp.isoformat(),'confirm_new_session':True})
    assert navigated['cursor'] == 1 and navigated['intent'] is None
    assert len(list(tmp_path.glob('replay-checkpoint-*.json'))) == 1
    resumed = app.command('resume', {'id':navigated['checkpoints'][0]['id']})
    for key in ['cursor','account','position','intent','decisions','trades']:
        assert resumed[key] == before[key]
    for field in ['symbol','side','quantity','order_type','price','stop_price','status']:
        assert resumed['pending'][0][field] == before['pending'][0][field]


def test_historical_view_is_read_only_and_keeps_earlier_prices():
    data = bars([(100+i,101+i,99+i,100+i) for i in range(100)])
    app = ReplayApp(data)
    app.command('create', {'strategy':'manual'})
    for _ in data:
        app.session.advance()
    before = app.session.snapshot()
    stamp = int(data[30].timestamp.replace(tzinfo=timezone.utc).timestamp())
    viewed = app.command('state', {'left':1,'right':5,'history_limit':100,'view_before':stamp})
    assert viewed['charts'][0][-1]['time'] == stamp
    assert viewed['charts'][0][-1]['close'] == 130
    assert viewed['cursor'] == before['cursor']
    assert viewed['account'] == before['account']
    with pytest.raises(ValueError, match='futureView'):
        app.command('state', {'view_before':stamp+100000})


@pytest.mark.parametrize('stop,target,legs', [(None,None,0),(90,None,1),(None,120,1),(90,120,2)])
def test_optional_protection_creates_only_explicit_native_orders(stop,target,legs):
    s = session([(100,101,99,100),(102,103,101,102),(104,105,103,104)])
    s.advance()
    s.place('BUY',1,'MARKET',stop,target)
    s.advance()
    assert s.strategy.active is not None
    assert len(s.engine.pending_orders) == legs
    assert (s.strategy.active['risk'] is None) == (stop is None)
    s.close()
    s.advance()
    trade = s.strategy.trades[0]
    assert trade['pnl'] == D(4)
    assert (trade['r'] is None) == (stop is None)
    assert s.scoreboard()['without_initial_risk'] == int(stop is None)


def test_protection_can_be_added_and_removed_without_inventing_initial_risk():
    s = session([(100,101,99,100),(102,103,101,102),(104,105,103,104)])
    s.advance()
    s.place('BUY',1,'MARKET')
    s.advance()
    s.modify(sl=95,tp=120)
    assert len(s.engine.pending_orders) == 2
    assert s.strategy.active['risk'] is None
    s.modify(sl='',tp='')
    assert not s.engine.pending_orders
    s.close()
    s.advance()
    assert s.scoreboard()['expectancy_r'] is None
    assert s.scoreboard()['total_r'] is None
    assert s.strategy.trades[0]['pnl'] == D(4)


def test_pending_optional_bracket_and_trailing_without_existing_stop():
    s = session([(100,101,99,100),(100,101,99,100),(104,105,103,104)])
    s.advance()
    s.place('BUY',1,'LIMIT',price=99)
    s.modify_pending(sl=90,tp=120)
    s.modify_pending(sl='',tp='')
    assert s.strategy.intent['sl'] is None and s.strategy.intent['tp'] is None
    s.advance()
    assert s.strategy.active
    s.modify(trailing=5)
    s._trail()
    assert s.strategy.active['sl'] == D(95)
    assert len(s.engine.pending_orders) == 1


def test_r_coverage_excludes_unprotected_trade_without_diluting_protected_r():
    s = session([(100,101,99,100)]*8)
    s.advance()
    s.place('BUY',1,'MARKET',90,120)
    s.advance()
    s.close();s.advance()
    s.place('SELL',1,'MARKET')
    s.advance()
    s.close();s.advance()
    metrics = s.scoreboard()
    assert metrics['trades'] == 2
    assert metrics['r_trades'] == 1
    assert metrics['without_initial_risk'] == 1
    assert metrics['expectancy_r'] == 0


def test_tp_only_limit_does_not_capture_target_before_fill():
    s = session([(100,101,99,100),(100,110,94,99),(99,106,98,105)])
    s.advance()
    s.place('BUY',1,'LIMIT',tp=105,price=95)
    s.advance()
    assert s.strategy.active is not None
    s.advance()
    assert s.strategy.trades[0]['exit'] == D(105)
    assert s.strategy.trades[0]['r'] is None
