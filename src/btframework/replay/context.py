"""Native higher-resolution context; never used for trade execution."""
from bisect import bisect_left, bisect_right
from datetime import timedelta, timezone

from btframework.replay.indicators import ChartIndicators


def stamp(value):
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def context_chart(session, minutes, limit):
    base=session.context_minutes
    now=stamp(session.now)
    stop=bisect_right(session._context_close_times,now)
    source=[{'time':stamp(bar.timestamp),'open':float(bar.open),'high':float(bar.high),'low':float(bar.low),'close':float(bar.close),'volume':bar.volume,'complete':True} for bar in session.context_bars[:stop]]
    current=now//(base*60)*(base*60)
    left=bisect_left(session._chart_bar_times,current)
    observed=session._chart_bars[left:len(session.history_bars)+session.cursor]
    if observed and stamp(observed[0].timestamp)==current and all(b.timestamp-a.timestamp==timedelta(minutes=session.base_minutes) for a,b in zip(observed,observed[1:])):
        source.append({'time':current,'open':float(observed[0].open),'high':float(max(b.high for b in observed)),'low':float(min(b.low for b in observed)),'close':float(observed[-1].close),'volume':sum(b.volume for b in observed),'complete':False})
    groups={}
    for row in source:
        bucket=row['time']//(minutes*60)*minutes*60
        group=groups.setdefault(bucket,[])
        group.append(row)
    result=[];indicators=ChartIndicators()
    for index,(time,rows) in enumerate(groups.items()):
        contiguous=rows[0]['time']==time and all(b['time']-a['time']==base*60 for a,b in zip(rows,rows[1:]))
        complete=contiguous and len(rows)==minutes//base and all(row['complete'] for row in rows)
        forming=contiguous and time+minutes*60>now
        if not complete and not forming:continue
        row={'time':time,'open':rows[0]['open'],'high':max(r['high'] for r in rows),'low':min(r['low'] for r in rows),'close':rows[-1]['close'],'volume':sum(r['volume'] for r in rows),'complete':complete,'index':index}
        row.update(indicators.update(row) if complete else indicators.preview(row))
        result.append(row)
    return result[-limit:]
