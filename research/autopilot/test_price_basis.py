"""Provider price-basis integrity regressions; NEVER fabricate daily OHLC."""
from __future__ import annotations

from datetime import datetime,timezone
from unittest.mock import patch
import unittest

from data_layer import _normalize, _from_yahoo
from research.autopilot.paper import _price_row, _valid_complete


class Response:
    status_code=200
    def json(self):
        ts=int(datetime(2026,11,18,16,0,tzinfo=timezone.utc).timestamp())
        return {"chart":{"result":[{
            "timestamp":[ts],
            "indicators":{
                "quote":[{
                    "open":[99.0],"high":[102.0],
                    "low":[98.0],"close":[100.0],"volume":[123456],
                }],
                "adjclose":[{"adjclose":[80.0]}],
            },
        }]}}


class PriceBasisContractTests(unittest.TestCase):
    def test_raw_close_beats_dividend_adjusted_close(self):
        rows=[{"date":"2026-11-18","Open":99,
               "High":102,"Low":98,"Close":100,"adjClose":80,
               "Volume":1000}]
        result=_normalize(rows,20)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["close"],100)
        self.assertEqual(result[0]["open"],99)

    def test_yahoo_quote_close_is_consistent_with_quote_ohlc(self):
        with patch("data_layer._polite_get", return_value=Response()):
            result=_from_yahoo("SPY",10)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["close"],100.0)
        self.assertEqual(result[0]["high"],102.0)
        self.assertEqual(result[0]["low"],98.0)
        self.assertIsNotNone(_price_row(result[0]))
        self.assertEqual(
            len(_valid_complete(result,"2026-11-18T23:30:00+00:00")),1)

    def test_missing_open_high_low_are_not_manufactured(self):
        result=_normalize([
            {"date":"2026-11-18","close":100,"volume":1000},
            {"date":"2026-11-18","open":99,"low":98,
             "close":100,"volume":1000},
        ],20)
        self.assertEqual(result,[])

    def test_adjusted_close_cannot_mix_with_raw_bars(self):
        result=_normalize([{"date":"2026-11-18",
                            "Open":99,"High":102,"Low":98,
                            "adjClose":80,"Volume":1000}],20)
        self.assertEqual(result,[])

    def test_complete_adjusted_ohlc_permitted_when_no_raw_series(self):
        result=_normalize([{"date":"2026-11-18",
                            "adjOpen":79,"adjHigh":82,"adjLow":78,
                            "adjClose":80,"volume":1000}],20)
        self.assertEqual(result[0]["close"],80)
        self.assertEqual(result[0]["high"],82)

    def test_inconsistent_or_nan_bar_dropped(self):
        bad=[{"date":"2026-11-18","open":100,
              "high":99,"low":98,"close":100,"volume":1000},
             {"date":"2026-11-17","open":"nan",
              "high":105,"low":98,"close":100,"volume":1000}]
        self.assertEqual(_normalize(bad,20),[])

    def test_normalize_does_not_mutate_original_prices(self):
        raw={"date":"2026-11-18","Open":99,"High":102,"Low":98,
             "Close":100,"adjClose":80,"volume":1000}
        _normalize([raw],20)
        self.assertEqual(raw["adjClose"],80)
        self.assertEqual(raw["Close"],100)


if __name__=="__main__":
    unittest.main()
