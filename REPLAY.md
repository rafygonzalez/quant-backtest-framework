# Replay Studio

Run the local dashboard with `btframework-replay --data history.csv`, or
`python -m btframework.replay.server --data history.csv`. Without `--data`, upload
CSV from the setup panel. The server binds only to `127.0.0.1:8050`; use `--port`
to change the port. Plotly is served locally; no CDN is required.

## Historical data

CSV columns: `timestamp,open,high,low,close,volume`. Timestamps mark bar **opens**
in UTC. Offset-aware input is normalized to UTC. Data must be chronological,
unique, finite and satisfy OHLCV invariants. Select the source resolution in
minutes, and an optional start/end window (end exclusive). Higher timeframes
aggregate only consumed bars, including the currently forming candle. Missing
bars are not synthesized.

Choose independent left/right timeframes. Both charts use the same replay cursor.
Play/pause, playback speed, next candle and next selected event control that cursor.
While a position is open, strategy events do not interrupt playback; closing the
trade pauses it. Manual pause remains available throughout.

## Instruments and trading

The instrument profile specifies tick, contract multiplier, quantity minimum/step,
settlement currency, margin, commission per unit per side and spread in price units.
The account and settlement currency are the same; cross-currency conversion is not
implicitly inferred. Profile defaults are editable simulation assumptions, not a
broker quote. Capital can start at 10 settlement-currency units. Futures require
whole contracts and fixed margin per contract; CFDs/forex use lot steps and notional
margin divided by configured leverage. Orders must fit the account's available
margin, and fills recheck margin including entry commission.

The side ticket offers Sell/Buy quotes, Market/Pending tabs, volume with quantity
steps, optional TP/SL prices, chart pickers and a single confirmation. Place
market, limit or stop entries with either, both or neither protective level. Orders submitted at the cursor are
eligible on the next base bar. SL/TP use native exchange OCO and the existing
fill-bar admissibility rules. A gap that invalidates entry geometry is rejected.
OHLC execution retains the engine's intrabar ambiguity; it is not tick reconstruction.

Drag entry/SL/TP level labels to amend a pending order. Indicator drawings remain
locked. With an open position, drag SL/TP,
edit their values, move SL to entry, configure a trailing distance or close at the
next base-bar open. Trailing uses observed closes and only tightens protection for
subsequent bars. Break even does not offset commissions or spread. Set trailing to
zero to disable it. One entry/position is active per session.

## Strategy adapters

The generic package never imports Quantira. Pass a trusted Python callable through
`--adapter module:function`. It receives native `Bar` values and returns
`(events, provisional_detector)`. Each `StrategyEvent` includes a unique ID, kind,
`known_at`, direction, reference price and provisional flag. The optional detector
is called once per consumed base bar with a one-element sequence. Its `event_kinds`
attribute can advertise all supported pause categories independently of the history.
Events and markers become visible only at `known_at`. Adapters must pass prefix
invariance tests and must not attach future outcomes to event metadata.

## Scoreboard and sessions

The scoreboard reports net closed-trade PnL, sample size, win rate, profit factor,
expectancy/total R, mark-to-market drawdown, and results by the event chosen at order
submission. Initial filled risk defines R even after stop amendments. Trades
without an initial SL have unavailable R even if a stop is later added;
`r_trades` and `without_initial_risk` expose coverage, and R statistics use only
the trades with defined initial risk. Their actual PnL remains in currency metrics. PnL includes
configured entry/exit commission and native spread. Profit factor is unavailable
when there are no losses. Open positions and pending orders at the end remain
unresolved and are excluded from closed-trade metrics.

Opportunities are classified as ordered, skipped, cancelled or occurring during
an occupied position. Skipped opportunities receive no hypothetical PnL.
Download a self-contained HTML report or the full JSON ledger. Save session exports
data, profile and command journal; restore reconstructs native engine state by
replaying that journal. Sessions contain the underlying history, so store them as
data files. Keep adapter/engine versions consistent when resuming.

## Engine API

`BacktestEngine.start()` initializes a fresh session. `step()` consumes one feed
step without resetting state. `result()` exposes the current result. `run()` uses
this same loop for batch execution. Replay pause is controlled outside the engine;
`stop()` retains its batch-stop semantics. Validated manual orders use
`submit_order()` and `cancel_orders()`.

## Prior context

A start date separates read-only prior bars from executable history. Source bars
before that date initialize chart averages and adapter state, not the account or
trade ledger. CSV and command-journal exports preserve this context. A history
upgrade can attach prior bars to an existing session without replaying its orders
or changing its cursor, decisions or account. Future adapter events are then
resolved from the warmed state; prior decisions retain their recorded identity.

MA9 and MA20 are simple close-price averages. Completed bars with missing base
intervals are excluded; a forming bar uses only its observed contiguous prefix.
The visible-candle selector, pan/zoom, price fit and chart focus control affect
presentation only. Plot indices remain stable as the display window rolls.

## Chart trading and study overlays

The graphical ticket picks entry, SL and TP levels by click, previews risk/reward,
and submits through the same validated native order path as the form. Price level
labels support dragging and keyboard tick adjustments. Replay pauses when a level
is dragged. Strategy overlays are optional and supplied by the adapter's point-in-time
visual contract; the generic framework never imports Quantira.

Chart ROC9/ROC45, cumulative OBV, normalized OBV20 flow and Wilder ATR14 are updated
causally from complete bars; forming candles preview a copy of that state. Higher
resolution context may be supplied independently from the execution feed, with an
explicit source name. Context bars become visible only when closed; the currently
forming context bar may use the already observed execution prefix. Context never
executes orders. Session exports retain its CSV and source.

The history display cap can be increased to 10,000 bars. This changes retrieval and
viewport only. Sparse source data remains sparse; it is not filled synthetically.
