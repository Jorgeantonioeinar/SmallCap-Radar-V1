import io
import csv
import unittest

from openpyxl import Workbook

from smart_engine import TitonSmartEngine
from ticker_from_text import extract_rows_from_csv, extract_rows_from_excel


class MoomooImportTests(unittest.TestCase):
    def test_real_moomoo_layout_uses_percent_change_and_keeps_all_columns(self):
        headers = [
            "Symbol", "Name", "Price", "Chg", "% Chg", "Mkt Cap", "Volume", "Turnover",
            "Bid", "Ask", "Free Float", "Bid Size", "Ask Size", "Open", "Prev Close",
            "High", "Low", "Vol Ratio", "Bid/Ask Ratio", "Range %", "P/E LFY",
            "Turnover %", "Change Rate", "5min Chg", "5D Chg", "10D Chg", "20D Chg",
            "60D Chg", "120D Chg", "250D Chg", "YTD Chg", "Industry",
        ]
        values = [
            "SDEV", "Stablecoin Development", "4.011", "+1.421", "+54.85%", "207,548,825",
            "53,514,903", "198,693,225", "4.000", "4.020", "23,685,858.00", "8,106",
            "1,226", "3.590", "2.590", "4.410", "3.480", "15.83", "73.72%", "35.91%",
            "Loss", "225.94%", "-0.08%", "+3.40%", "+211.39%", "+377.52%", "+367.57%",
            "+167.79%", "+151.06%", "-47.49%", "-85.78%", "Asset Management",
        ]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(headers)
        writer.writerow(values)

        rows = extract_rows_from_csv(buffer.getvalue().encode("utf-8"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["gap_override"], 54.85)  # not the absolute +1.421 Chg column
        self.assertEqual(row["momo_data"]["change"], 1.421)
        self.assertEqual(row["rvol_override"], 15.83)
        self.assertEqual(row["market_cap_override"], 207_548_825)
        self.assertEqual(row["volume_override"], 53_514_903)
        self.assertNotIn("premarket_volume_override", row)  # Moomoo export has Volume, not PM Volume
        self.assertEqual(row["momo_data"]["prev_close"], 2.59)
        self.assertEqual(row["momo_data"]["five_day_change"], 211.39)
        self.assertEqual(row["momo_data"]["industry"], "Asset Management")
        self.assertEqual(len(row["momo_data"]), 32)

    def test_csv_extracts_pre_market_fields_and_prefers_premarket_gap(self):
        csv_text = (
            "Symbol,% Chg,Pre Mkt % Chg,Vol Ratio,Pre Mkt Stock Price,Float Shares,Pre Mkt Vol,Market Cap\n"
            "ABCD,4.0%,12.5%,3.2x,$2.75,2.3M,450K,28.5M\n"
        )
        rows = extract_rows_from_csv(csv_text.encode("utf-8"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["symbol"], "ABCD")
        self.assertEqual(row["gap_override"], 12.5)
        self.assertEqual(row["rvol_override"], 3.2)
        self.assertEqual(row["price_hint"], 2.75)
        self.assertEqual(row["price_session"], "premarket")
        self.assertEqual(row["float_override"], 2_300_000)
        self.assertEqual(row["premarket_volume_override"], 450_000)
        self.assertEqual(row["market_cap_override"], 28_500_000)

    def test_shuffled_afterhours_columns_are_matched_by_header_name(self):
        csv_text = (
            "After Hours Volume,Symbol,After Hours Price,% Chg,After Hours % Chg,Vol Ratio,Mkt Cap,Free Float\n"
            "550000,ABCD,3.50,4.0%,8.2%,2.1x,28.5M,2.3M\n"
        )
        row = extract_rows_from_csv(csv_text.encode("utf-8"))[0]
        self.assertEqual(row["symbol"], "ABCD")
        self.assertEqual(row["gap_override"], 8.2)
        self.assertEqual(row["price_hint"], 3.5)
        self.assertEqual(row["afterhours_volume_override"], 550_000)
        self.assertEqual(row["market_cap_override"], 28_500_000)
        self.assertEqual(row["momo_data"]["afterhours_change_pct"], 8.2)
        self.assertEqual(row["momo_data"]["change_pct"], 4.0)

    def test_actual_post_mkt_stock_price_header_overrides_generic_price(self):
        csv_text = (
            "Symbol,Price,Chg,% Chg,Mkt Cap,Volume,Post Mkt Stock Price,"
            "Post Mkt % Chg,Vol Ratio,Prev Close\n"
            "AGRZ,3.25,0.02,0.62%,6151191,21258,3.47,6.77%,0.03,3.23\n"
        )
        row = extract_rows_from_csv(csv_text.encode("utf-8"))[0]
        self.assertEqual(row["gap_override"], 6.77)
        self.assertEqual(row["price_hint"], 3.47)
        self.assertEqual(row["price_session"], "afterhours")
        self.assertEqual(row["momo_data"]["price"], 3.25)
        self.assertEqual(row["momo_data"]["afterhours_price"], 3.47)
        self.assertEqual(row["volume_override"], 21258)

    def test_post_market_gap_derives_from_post_market_price_before_regular_percent(self):
        csv_text = (
            "Symbol,Price,% Chg,Post Mkt Stock Price,Prev Close\n"
            "AGRZ,3.25,0.62%,3.47,3.23\n"
        )
        row = extract_rows_from_csv(csv_text.encode("utf-8"))[0]
        self.assertAlmostEqual(row["gap_override"], (3.47 - 3.23) / 3.23 * 100.0)

    def test_explicit_symbol_column_keeps_valid_common_word_and_one_letter_symbols(self):
        csv_text = "Symbol,Price\nALL,3.20\nBIO,4.10\nA,1.25\n"
        rows = extract_rows_from_csv(csv_text.encode("utf-8"))
        self.assertEqual([row["symbol"] for row in rows], ["ALL", "BIO", "A"])

    def test_premarket_export_uses_premarket_gap_and_price_and_keeps_csv_ticker(self):
        csv_text = (
            "Symbol,% Chg,Pre Mkt % Chg,Price,Pre Mkt Stock Price,Vol Ratio,Volume,Mkt Cap\n"
            "CSV,-20.81%,41.59%,21.88,30.98,0.00,297362,1,217,477,351\n"
        )
        # Quote a thousands-separated number as Moomoo does in its CSV export.
        csv_text = csv_text.replace("297362,1,217,477,351", '297362,"1,217,477,351"')
        row = extract_rows_from_csv(csv_text.encode("utf-8"))[0]
        self.assertEqual(row["symbol"], "CSV")
        self.assertEqual(row["gap_override"], 41.59)
        self.assertEqual(row["price_hint"], 30.98)
        self.assertEqual(row["rvol_override"], 0.0)
        self.assertEqual(row["volume_override"], 297_362)
        self.assertEqual(row["market_cap_override"], 1_217_477_351)
        self.assertEqual(row["momo_data"]["change_pct"], -20.81)

    def test_xlsx_reads_formatted_percentage_and_moomoo_columns(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Screener"
        sheet.append(["Symbol", "Pre Mkt % Chg", "% Chg", "Vol Ratio", "Pre Mkt Stock Price", "Float Shares", "Pre Mkt Vol"])
        sheet.append(["WXYZ", 0.1735, 0.04, 2.8, 3.14, 1_900_000, 710_000])
        sheet["B2"].number_format = "0.00%"
        sheet["C2"].number_format = "0.00%"
        buffer = io.BytesIO()
        workbook.save(buffer)

        rows = extract_rows_from_excel(buffer.getvalue(), filename="momo.xlsx")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["symbol"], "WXYZ")
        self.assertAlmostEqual(row["gap_override"], 17.35)
        self.assertAlmostEqual(row["rvol_override"], 2.8)
        self.assertEqual(row["price_hint"], 3.14)
        self.assertEqual(row["float_override"], 1_900_000)
        self.assertEqual(row["premarket_volume_override"], 710_000)

    def test_smart_engine_uses_imported_gap_instead_of_recomputed_zero(self):
        result = TitonSmartEngine().evaluate({
            "ticker": "ABCD",
            "current_price": 2.50,
            "prev_close": 2.50,
            "gap_pct": 18.0,
            "market_cap": 40_000_000,
            "float_shares": 8_000_000,
            "shares_outstanding": 12_000_000,
            "session_volume": 2_000_000,
            "catalyst_verified": True,
            "sec_dilution_blocked": False,
        })
        self.assertEqual(result["gap_pct"], 18.0)
        self.assertNotEqual(result["signal"], "RECHAZADO")


if __name__ == "__main__":
    unittest.main()
