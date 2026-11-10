"""Two-Clock Dislocation Lab v0.1 -- research only, never places orders.

Hypothesis (NOT a proven edge): a verified primary-source company event can
arrive before the equity fully reflects it. An independently estimated market-
response counterfactual may distinguish delayed repricing from ordinary price
fluctuation. Subsequent *asymmetric response* across market-down and market-up
days may support or falsify that hypothesis.

Daily OHLCV can never reveal true buyers, sellers, queue depth, actual fills,
or hidden orders. This module intentionally calls its price/volume measurements
PROXIES. It returns NO probabilities, no BUY signal, and no fabricated profits.

Inputs: chronological unadjusted/consistently adjusted daily OHLCV bars and
public event records carrying their true FIRST-OBSERVED timestamp. All
features are point-in-time. Never consume observations after `asof_utc`.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, time, timezone
import math
from statistics import median
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
RESEARCH_VERSION = 'two_clock_dislocation_v0_1'
MIN_ESTIMATION_RETURNS = 45
MIN_DOLLAR_VOLUME_20 = 1_000_000.0


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _instant(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z','+00:00'))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def _daily(records: Sequence[Mapping[str, Any]], asof: datetime) -> dict[str, dict] | None:
    """Fail closed on malformed bars; don't silently forward-fill or mix bases."""
    out={}
    for raw in records:
        if not isinstance(raw, Mapping):
            return None
        try:
            d=date.fromisoformat(str(raw['date'])[:10])
        except (KeyError, ValueError, TypeError):
            return None
        # A daily bar is only considered complete AFTER 4:00 pm ET.
        complete_at=datetime.combine(d,time(16,0),tzinfo=ET).astimezone(timezone.utc)
        if complete_at>asof:
            continue
        if d.isoformat() in out:  # collisions can imply split or provider corruption
            return None
        o,h,l,c,v=(_finite(raw.get(k)) for k in ('open','high','low','close','volume'))
        if any(x is None for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0:
            return None
        if not l<=min(o,c)<=max(o,c)<=h:
            return None
        out[d.isoformat()]={'date':d.isoformat(),'open':o,'high':h,'low':l,'close':c,'volume':v}
    return out


def _paired(stock: Mapping[str,dict], benchmark: Mapping[str,dict]) -> list[dict]:
    """No forward-filled benchmark days and no synthetic zero-volume rows."""
    days=sorted(set(stock)&set(benchmark))
    pairs=[]
    for previous,current in zip(days,days[1:]):
        a,b=stock[previous],stock[current]
        m,n=benchmark[previous],benchmark[current]
        # Reject apparent >90% moves as unverified split/corporate action.
        if min(b['close']/a['close'],n['close']/m['close'])<.1:
            return []
        if max(b['close']/a['close'],n['close']/m['close'])>10:
            return []
        pairs.append({**b,'benchmark_ret':n['close']/m['close']-1,
                      'stock_ret':b['close']/a['close']-1})
    return pairs


def _beta(prior: Sequence[dict]) -> float | None:
    if len(prior)<MIN_ESTIMATION_RETURNS:
        return None
    x=[r['benchmark_ret'] for r in prior]
    y=[r['stock_ret'] for r in prior]
    mx=sum(x)/len(x)
    my=sum(y)/len(y)
    variance=sum((v-mx)**2 for v in x)
    if variance < .0001:  # no identifiable benchmark behavior
        return None
    raw=sum((a-mx)*(b-my) for a,b in zip(x,y))/variance
    # Winsorized low-data beta, not a prediction model or source of alpha.
    return max(-.5,min(3.0,raw))


def _recent_verified_event(events: Sequence[Mapping[str,Any]], asof:datetime) -> dict | None:
    choices=[]
    for e in events:
        if not isinstance(e,Mapping):
            continue
        ts=_instant(e.get('observed_at'))
        if not ts or ts>asof or asof-ts>timedelta(days=21):
            continue
        # Only human-verified/externally adjudicated primary-source positives.
        # A raw generic 8-K and sentiment-only headlines NEVER qualify.
        if (e.get('source')!='sec_primary' or
            e.get('evidence_status')!='verified' or
            e.get('polarity')!='positive' or
            e.get('event_id') in (None,'')):
            continue
        choices.append({'event_id':str(e['event_id']),
                        'observed_at':ts,
                        'kind':str(e.get('kind') or 'unknown')})
    return max(choices,key=lambda e:e['observed_at']) if choices else None


def analyze(*, stock_bars:Sequence[Mapping[str,Any]],
            benchmark_bars:Sequence[Mapping[str,Any]],
            observed_events:Sequence[Mapping[str,Any]],
            asof_utc:str, exchange_verified:bool=False,
            broker_eligible_verified:bool=False,
            dilution_clear_verified:bool=False) -> dict:
    """Return diagnostic features. No strategy can trade solely on this output.

    Core *novel-to-this-project* hypothesis:
    (1) an independent verified information clock ticks first;
    (2) the equity's *counterfactual* market-adjusted reaction remains small;
    (3) on completed bars, the stock survives market-down shocks and responds
        more readily upward per unit of relative trading volume;
    (4) the second clock then catches up -- measured only prospectively.
    """
    base={'version':RESEARCH_VERSION,'status':'abstain',
          'probability':None,'trade_action':'none','reason':None,
          'execution_ready':False}
    asof=_instant(asof_utc)
    if not asof:
        return {**base,'reason':'asof_must_be_offset_aware_utc'}
    stock=_daily(stock_bars,asof)
    benchmark=_daily(benchmark_bars,asof)
    if stock is None or benchmark is None:
        return {**base,'reason':'malformed_or_duplicate_ohlcv'}
    # Trading-session continuity is essential: a missing stock bar must not
    # turn two different trading days into a misleading one-day return.
    bdays=sorted(benchmark)
    recent_benchmark_days=bdays[-96:]
    if len(recent_benchmark_days)<96 or any(d not in stock for d in recent_benchmark_days):
        return {**base,'reason':'missing_recent_exchange_sessions'}
    paired=_paired(stock,benchmark)
    if len(paired)<80:
        return {**base,'reason':'insufficient_or_discontinuous_daily_history',
                'paired_returns':len(paired)}
    # Estimate beta from observations BEFORE the 15-day research window.
    study=paired[-15:]
    training=paired[-95:-15]
    beta=_beta(training)
    if beta is None:
        return {**base,'reason':'benchmark_beta_unidentified'}
    prev20=paired[-35:-15]
    dollar_volume=median([r['close']*r['volume'] for r in prev20])
    if dollar_volume<MIN_DOLLAR_VOLUME_20:
        return {**base,'reason':'inadequate_liquidity_proxy',
                'median_dollar_volume_20':round(dollar_volume,2)}
    volume_median=median([r['volume'] for r in prev20])
    if volume_median<=0:
        return {**base,'reason':'missing_volume_baseline'}
    for r in study:
        r['residual']=r['stock_ret']-beta*r['benchmark_ret']
        r['rel_vol']=r['volume']/volume_median
    # Proxies for supply absorption and response asymmetry, not true orderflow.
    down=[r for r in study if r['benchmark_ret']<=-.004]
    if len(down)<2:
        return {**base,'reason':'insufficient_down_market_shocks',
                'observed_down_shocks':len(down)}
    upside=[r for r in study if r['residual']>.002]
    downside=[r for r in study if r['residual']<-.002]
    if len(upside)<2 or len(downside)<2:
        return {**base,'reason':'insufficient_two_sided_responses'}
    resilience=median([r['residual'] for r in down])
    upside_impact=median([r['residual']/max(.2,r['rel_vol']) for r in upside])
    downside_impact=median([-r['residual']/max(.2,r['rel_vol']) for r in downside])
    response_asymmetry=upside_impact/max(.0005,downside_impact)
    event=_recent_verified_event(observed_events,asof)
    if event is None:
        return {**base,'reason':'no_recent_verified_primary_positive_event',
                'beta':round(beta,3),
                'resilience_proxy':round(resilience,5),
                'response_asymmetry_proxy':round(response_asymmetry,3)}
    # Under a strict no-lookahead interpretation, react only from first
    # *completed daily session after* the first observed event timestamp.
    event_day=event['observed_at'].astimezone(ET).date().isoformat()
    responses=[r for r in study if r['date']>event_day]
    if not responses:
        return {**base,'reason':'awaiting_first_complete_post_event_bar',
                'event_id':event['event_id']}
    # Counterfactual response: additive residuals for small daily returns.
    # This is a research proxy; nonlinear compounding is handled in returns.
    cumulative_residual=sum(r['residual'] for r in responses)
    recent_runup=stock[study[-1]['date']]['close']/stock[study[0]['date']]['close']-1
    # Do not emit a buy recommendation: candidate means only 'priority research'.
    status=('dislocation_candidate' if resilience>.002 and
            response_asymmetry>1.25 and -.05<=cumulative_residual<=.025 and
            recent_runup<.20 else 'no_dislocation')
    eligibility=bool(exchange_verified and broker_eligible_verified and dilution_clear_verified)
    return {
        **base,'status':status,'reason':'observed_proxy_only',
        'asof_utc':asof.isoformat(),
        'event_id':event['event_id'],'event_kind':event['kind'],
        'event_first_seen_utc':event['observed_at'].isoformat(),
        'completed_post_event_bars':len(responses),
        'beta':round(beta,3),
        'resilience_proxy':round(resilience,6),
        'response_asymmetry_proxy':round(response_asymmetry,3),
        'event_residual_return_proxy':round(cumulative_residual,6),
        'last_15d_runup':round(recent_runup,4),
        'median_dollar_volume_20':round(dollar_volume,2),
        'observed_down_shocks':len(down),
        'eligibility_inputs_verified':eligibility,
        'execution_ready':False, # requires a separate broker-spread/fill engine
        'probability':None,
        'trade_action':'none',
        'research_note':'OHLCV is not Level-2 orderflow. No real-time prices, causal profit proof or automatic trades.'
    }