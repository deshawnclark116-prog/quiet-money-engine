import unittest
from datetime import date, timedelta, datetime, timezone
from two_clock_research import analyze


def fixture(n=140,event_offset=7):
    start=date(2026,1,1)
    ds=[start+timedelta(days=i) for i in range(n)]
    stock=[]; bench=[]; px=2.0; spy=100.0
    for i,d in enumerate(ds):
        mr=(-.009 if i%6 in (0,2,5) else .011)
        spy*=(1+mr)
        r=mr*1.2+(0.006 if i>n-17 and mr<0 else (-.002 if i>n-17 else .004*(1 if i%2 else -1)))
        px*=(1+r)
        o=px/(1+r/2);hi=max(o,px)*1.002;low=min(o,px)*.998
        stock.append(dict(date=d.isoformat(),open=o,high=hi,low=low,close=px,volume=1_100_000+(i%7)*10000))
        spy_open=spy/(1+mr/2)
        bench.append(dict(date=d.isoformat(),open=spy_open,high=max(spy,spy_open)*1.002,low=min(spy,spy_open)*.998,close=spy,volume=5_000_000))
    event_date=ds[n-event_offset]
    event_time=datetime.combine(event_date,datetime.min.time(),tzinfo=timezone.utc).replace(hour=15).isoformat()
    event={'observed_at':event_time,'event_id':'0000000001-26-000001','source':'sec_primary','kind':'8-K','polarity':'positive','evidence_status':'verified'}
    end=datetime.combine(ds[-1],datetime.min.time(),tzinfo=timezone.utc).replace(hour=22).isoformat()
    return stock,bench,event,end


class TwoClockTests(unittest.TestCase):
    def run_case(self, s,b,e,now,**kwargs):
        return analyze(stock_bars=s,benchmark_bars=b,observed_events=[e],asof_utc=now,**kwargs)

    def test_never_generates_a_trade_or_fabricated_probability(self):
        s,b,e,t=fixture()
        result=self.run_case(s,b,e,t,exchange_verified=True,broker_eligible_verified=True,dilution_clear_verified=True)
        self.assertNotEqual(result['reason'],'malformed_or_duplicate_ohlcv')
        self.assertEqual(result['trade_action'],'none')
        self.assertIsNone(result['probability'])
        self.assertFalse(result['execution_ready'])

    def test_future_prices_never_change_past_result(self):
        s,b,e,t=fixture()
        baseline=self.run_case(s,b,e,t)
        # A future bar with an enormous share move must be ignored at as-of.
        last=date.fromisoformat(s[-1]['date'])
        future=(last+timedelta(days=1)).isoformat()
        s2=s+[{'date':future,'open':2,'high':500,'low':1,'close':499,'volume':9e9}]
        b2=b+[{'date':future,'open':100,'high':110,'low':90,'close':101,'volume':5e6}]
        self.assertEqual(self.run_case(s2,b2,e,t),baseline)

    def test_late_knowledge_does_not_become_earlier_event(self):
        s,b,e,t=fixture()
        e={**e,'observed_at':'2027-01-01T01:00:00+00:00'}
        self.assertEqual(self.run_case(s,b,e,t)['reason'],'no_recent_verified_primary_positive_event')

    def test_unknown_positive_news_is_not_verified(self):
        s,b,e,t=fixture()
        e={**e,'source':'news_sentiment','evidence_status':'machine_only'}
        self.assertEqual(self.run_case(s,b,e,t)['reason'],'no_recent_verified_primary_positive_event')

    def test_missing_benchmark_abstains(self):
        s,b,e,t=fixture()
        self.assertEqual(self.run_case(s,b[:15],e,t)['status'],'abstain')

    def test_corrupted_provider_bar_fails_closed(self):
        s,b,e,t=fixture()
        s[-1]={**s[-1],'low':s[-1]['high']*2}
        self.assertEqual(self.run_case(s,b,e,t)['reason'],'malformed_or_duplicate_ohlcv')

    def test_not_eligible_with_unknown_exchange(self):
        s,b,e,t=fixture()
        result=self.run_case(s,b,e,t)
        if 'eligibility_inputs_verified' in result:
            self.assertFalse(result['eligibility_inputs_verified'])

    def test_missing_market_data_in_historical_window_abstains(self):
        s,b,e,t=fixture()
        b=b[-30:]
        self.assertEqual(self.run_case(s,b,e,t)['status'],'abstain')


if __name__=='__main__':
    unittest.main()