import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd

import config
from smart_engine import TitonSmartEngine, TitonSmartEngineConfig

# Keep the scorer tests isolated from data-source integrations. The scoring
# functions receive this small fake fetcher and never access a network.
_data_fetcher_stub = types.ModuleType("data_fetcher")
_data_fetcher_stub.DataFetcher = type("DataFetcher", (), {})
sys.modules["data_fetcher"] = _data_fetcher_stub
import screener  # noqa: E402


class FakeFetcher:
    def get_latest_price(self, symbol):
        return 5.0

    def get_fundamentals(self, symbol):
        return {}

    def get_bars(self, symbol, minutes_back=None):
        return pd.DataFrame()

    def get_volume_metrics(self, symbol):
        return {}

    def get_company_country(self, symbol):
        return None


class SmartFakeFetcher(FakeFetcher):
    def __init__(self):
        self.latest_calls = 0

    def get_latest_price(self, symbol):
        self.latest_calls += 1
        return 5.0

    def get_fundamentals(self, symbol):
        return {"shares_outstanding": 3_000_000}

    def get_previous_regular_close(self, symbol):
        return 3.23


class ScoringWeightTests(unittest.TestCase):
    def test_scalping_classic_weights_prioritize_gap_and_volume(self):
        weights = config.CLASSIC_SCORING_WEIGHTS["scalping"]
        self.assertEqual(weights["gap"] + weights["rvol"] + weights["float_low"] + weights["rsi"], 10.0)
        self.assertEqual(weights["gap"] + weights["rvol"] + weights["float_standard"] + weights["rsi"], 9.3)
        self.assertEqual(weights["gap"], 3.5)
        self.assertEqual(weights["rvol"], 3.5)
        self.assertEqual(weights["gap"] + weights["rvol"], 7.0)

    def test_swing_classic_profile_preserves_previous_maximums(self):
        weights = config.CLASSIC_SCORING_WEIGHTS["swing"]
        self.assertEqual(weights["gap"] + weights["rvol"] + weights["float_low"] + weights["rsi"], 10.0)
        self.assertEqual(weights["gap"], 3.0)
        self.assertEqual(weights["rvol"], 3.0)
        self.assertEqual(weights["float_low"], 2.5)

    def test_classic_scalping_result_exposes_gap_and_rvol_points(self):
        old_values = {
            "EXIT_MODE": config.EXIT_MODE,
            "SCORING_ENGINE": config.SCORING_ENGINE,
            "FAST_SCREENING": config.FAST_SCREENING,
        }
        old_helpers = {
            "resolve_active_session": screener.resolve_active_session,
            "compute_rsi": screener.compute_rsi,
            "compute_atr": screener.compute_atr,
            "_finalize_candidate": screener._finalize_candidate,
        }
        try:
            config.EXIT_MODE = "scalping"
            config.SCORING_ENGINE = "classic"
            config.FAST_SCREENING = True
            screener.resolve_active_session = lambda mode: "regular"
            screener.compute_rsi = lambda bars: 65.0
            screener.compute_atr = lambda bars: 0.1
            screener._finalize_candidate = lambda result, **kwargs: result

            result = screener.score_candidate(
                "TEST", FakeFetcher(), float_override=5_000_000,
                rvol_override=6.0, gap_override=55.0, price_override=5.0,
            )
            self.assertEqual(result["score"], 10.0)
            self.assertEqual(result["gap_score"], 3.5)
            self.assertEqual(result["volume_score"], 3.5)
            self.assertEqual(result["score_components"]["float"], 1.5)
            self.assertEqual(result["score_components"]["rsi"], 1.5)

            # Moomoo premarket/regular exports can report Vol Ratio = 0 while
            # still carrying useful session Volume. Classic must consume it.
            result_from_volume = screener.score_candidate(
                "TEST", FakeFetcher(), float_override=2_000_000,
                rvol_override=0.0, gap_override=55.0, price_override=5.0,
                volume_override=1_000_000,
            )
            self.assertEqual(result_from_volume["rvol"], 0.0)
            self.assertEqual(result_from_volume["session_volume"], 1_000_000.0)
            self.assertEqual(result_from_volume["volume_score"], 3.5)
            self.assertEqual(result_from_volume["volume_score_basis"], "volumen de sesión / float")
            self.assertEqual(result_from_volume["score"], 10.0)

            with patch("strategy.get_current_session", return_value="premarket"):
                result_premarket = screener.score_candidate(
                    "TEST", FakeFetcher(), float_override=2_000_000,
                    rvol_override=0.0, gap_override=55.0, price_override=5.0,
                    volume_override=600_000, premarket_volume_override=1_000_000,
                )
            self.assertEqual(result_premarket["session_volume"], 1_000_000.0)
            self.assertEqual(result_premarket["volume_score"], 3.5)

            with patch("strategy.get_current_session", return_value="afterhours"):
                result_afterhours = screener.score_candidate(
                    "TEST", FakeFetcher(), float_override=2_000_000,
                    rvol_override=0.0, gap_override=55.0, price_override=3.47,
                    volume_override=600_000, afterhours_volume_override=1_500_000,
                    price_session_override="afterhours",
                )
            self.assertEqual(result_afterhours["session_volume"], 1_500_000.0)
            self.assertEqual(result_afterhours["price"], 3.47)
            self.assertEqual(result_afterhours["volume_score"], 3.5)
            with patch("strategy.get_current_session", return_value="afterhours"):
                result_low_positive_rvol = screener.score_candidate(
                    "TEST", FakeFetcher(), float_override=2_000_000,
                    rvol_override=0.03, gap_override=55.0, price_override=3.47,
                    volume_override=600_000, afterhours_volume_override=1_500_000,
                    price_session_override="afterhours",
                )
            self.assertEqual(result_low_positive_rvol["rvol"], 0.03)
            self.assertEqual(result_low_positive_rvol["volume_score"], 3.5)
            self.assertEqual(result_low_positive_rvol["volume_score_basis"], "volumen de sesión / float")
            self.assertTrue(any("alternativa" in note for note in result_low_positive_rvol["notes"]))

            with patch("strategy.get_current_session", return_value="regular"):
                result_mismatched_price = screener.score_candidate(
                    "TEST", FakeFetcher(), float_override=2_000_000,
                    rvol_override=6.0, gap_override=55.0, price_override=3.47,
                    price_session_override="afterhours",
                )
            self.assertEqual(result_mismatched_price["price"], 5.0)
            self.assertTrue(any("no aplicado" in note for note in result_mismatched_price["notes"]))
        finally:
            for key, value in old_values.items():
                setattr(config, key, value)
            for key, value in old_helpers.items():
                setattr(screener, key, value)

    def test_smart_engine_weights_gap_and_session_volume_and_reports_components(self):
        cfg = TitonSmartEngineConfig()
        self.assertAlmostEqual(
            cfg.weight_market_cap + cfg.weight_float_turnover + cfg.weight_catalyst_gap
            + cfg.weight_structural_rvol + cfg.weight_price_structure,
            10.0,
        )
        result = TitonSmartEngine(cfg).evaluate({
            "ticker": "TEST",
            "current_price": 5.0,
            "prev_close": 4.0,
            "gap_pct": 20.0,
            "market_cap": 40_000_000,
            "float_shares": 8_000_000,
            "shares_outstanding": 10_000_000,
            "session_volume": 4_000_000,
            "catalyst_verified": False,
            "sec_dilution_blocked": False,
        })
        self.assertEqual(result["score_components"]["gap"], 3.6)
        self.assertEqual(result["score_components"]["volume"], 3.5)
        self.assertEqual(result["score"], 9.1)

    def test_smart_screener_uses_afterhours_price_volume_and_session_threshold(self):
        old_values = {
            "SCORING_ENGINE": config.SCORING_ENGINE,
            "FAST_SCREENING": config.FAST_SCREENING,
        }
        old_helpers = {
            "compute_rsi": screener.compute_rsi,
            "compute_atr": screener.compute_atr,
            "_finalize_candidate": screener._finalize_candidate,
        }
        fetcher = SmartFakeFetcher()
        try:
            config.SCORING_ENGINE = "smart"
            config.FAST_SCREENING = True
            screener.compute_rsi = lambda bars: None
            screener.compute_atr = lambda bars: None
            screener._finalize_candidate = lambda result, **kwargs: result
            with (
                patch("strategy.get_current_session", return_value="afterhours"),
                patch("strategy.get_premarket_high", return_value=None),
                patch("strategy.compute_vwap", return_value=None),
                patch("strategy.compute_entry_score", return_value=0.0),
                patch("strategy.classify_chase_risk", return_value="SIN_DATOS"),
                patch("strategy.compute_extension_metrics", return_value={}),
            ):
                result = screener.score_candidate(
                    "TEST", fetcher, float_override=2_000_000, rvol_override=0.0,
                    gap_override=8.5, price_override=0.50, market_cap_override=40_000_000,
                    volume_override=50_000, afterhours_volume_override=500_000,
                    price_session_override="afterhours",
                )
            self.assertEqual(result["price"], 0.50)  # >0.01 penny-stock floor and session-matched
            self.assertEqual(result["gap_pct"], 8.5)  # AH threshold is 8%, not static 10%
            self.assertEqual(result["session_volume"], 500_000)
            self.assertEqual(result["rvol"], 0.25)
            self.assertEqual(result["volume_score_basis"], "volumen de sesión / float")
            self.assertEqual(fetcher.latest_calls, 0)  # no stale regular quote replaces the AH snapshot
        finally:
            for key, value in old_values.items():
                setattr(config, key, value)
            for key, value in old_helpers.items():
                setattr(screener, key, value)


if __name__ == "__main__":
    unittest.main()
