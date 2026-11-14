"""Causal chart indicators, independent of a strategy or vendor."""
from collections import deque
from copy import deepcopy


class ChartIndicators:
    def __init__(self):
        self.closes = deque(maxlen=46)
        self.signed_volume = deque(maxlen=20)
        self.volume = deque(maxlen=20)
        self.tr = deque(maxlen=14)
        self.obv = 0.0
        self.atr = None

    def update(self, row):
        close = row['close']
        previous = self.closes[-1] if self.closes else None
        signed = row['volume'] * (1 if close > previous else -1 if close < previous else 0) if previous is not None else 0.0
        self.obv += signed
        self.signed_volume.append(signed)
        self.volume.append(row['volume'])
        true_range = max(row['high']-row['low'], abs(row['high']-previous), abs(row['low']-previous)) if previous is not None else row['high']-row['low']
        self.tr.append(true_range)
        if self.atr is not None:
            self.atr = (self.atr*13+true_range)/14
        elif len(self.tr)==14:
            self.atr = sum(self.tr)/14
        self.closes.append(close)
        values = list(self.closes)
        result = {'obv': self.obv, 'obv_flow': sum(self.signed_volume)/sum(self.volume) if len(self.volume)==20 and sum(self.volume)>0 else None,
                  'atr14': self.atr}
        for period in (9,20):
            result[f'ma{period}'] = sum(values[-period:])/period if len(values)>=period else None
        for period in (9,45):
            result[f'roc{period}'] = 100*(close-values[-period-1])/values[-period-1] if len(values)>period and values[-period-1] else None
        return result

    def preview(self, row):
        return deepcopy(self).update(row)
