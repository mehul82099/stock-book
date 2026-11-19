#!/usr/bin/env python3
"""Tests for stock_book. Run: python -m unittest -v"""

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import date
from decimal import Decimal
from pathlib import Path

import stock_book as sb


class ParseTests(unittest.TestCase):
    def test_parse_qty(self):
        self.assertEqual(sb.parse_qty("2.5"), Decimal("2.5"))
        self.assertEqual(sb.parse_qty("10"), Decimal("10"))

    def test_parse_qty_rejects(self):
        for bad in ("0", "-1", "abc", ""):
            with self.assertRaises(sb.StockBookError):
                sb.parse_qty(bad)

    def test_parse_level(self):
        self.assertEqual(sb.parse_level("0"), Decimal("0"))
        self.assertEqual(sb.parse_level("5"), Decimal("5"))

    def test_parse_level_rejects(self):
        for bad in ("-1", "abc"):
            with self.assertRaises(sb.StockBookError):
                sb.parse_level(bad)

    def test_fmt_qty(self):
        self.assertEqual(sb.fmt_qty(Decimal("2.500")), "2.5")
        self.assertEqual(sb.fmt_qty(Decimal("10")), "10")

    def test_bad_date(self):
        with self.assertRaises(sb.StockBookError):
            sb.parse_date("19/11/2026")


class BookFixture(unittest.TestCase):
    def setUp(self):
        self.book = sb.StockBook()
        self.book.add_item("Parle-G Biscuits", "packet", Decimal("20"),
                           supplier="Sharma Wholesale")
        self.book.add_item("Amul Milk 500ml", "packet", Decimal("10"))


class ItemTests(BookFixture):
    def test_add_and_lookup(self):
        i = self.book.get_item("parle-g biscuits")
        self.assertEqual(i["id"], "t1")
        self.assertEqual(i["supplier"], "Sharma Wholesale")

    def test_duplicate_rejected(self):
        with self.assertRaises(sb.StockBookError):
            self.book.add_item("AMUL MILK 500ML")

    def test_empty_name_rejected(self):
        with self.assertRaises(sb.StockBookError):
            self.book.add_item("  ")

    def test_unknown_item(self):
        with self.assertRaises(sb.StockBookError):
            self.book.get_item("t99")

    def test_set_reorder_level(self):
        i = self.book.set_reorder_level("t2", "15")
        self.assertEqual(i["reorder_level"], "15")


class MovementTests(BookFixture):
    def test_in_out_stock(self):
        self.book.record("t1", "in", Decimal("50"), date(2026, 11, 10))
        self.book.record("t1", "out", Decimal("12"), date(2026, 11, 11))
        self.assertEqual(self.book.stock("t1"), Decimal("38"))

    def test_decimal_quantities(self):
        self.book.add_item("Sugar", "kg")
        self.book.record("t3", "in", Decimal("10.5"), date(2026, 11, 10))
        self.book.record("t3", "out", Decimal("2.25"), date(2026, 11, 11))
        self.assertEqual(self.book.stock("t3"), Decimal("8.25"))

    def test_out_cannot_go_negative(self):
        self.book.record("t1", "in", Decimal("10"), date(2026, 11, 10))
        with self.assertRaises(sb.StockBookError) as ctx:
            self.book.record("t1", "out", Decimal("11"), date(2026, 11, 11))
        self.assertIn("only 10 in stock", str(ctx.exception))

    def test_out_on_empty_rejected(self):
        with self.assertRaises(sb.StockBookError):
            self.book.record("t2", "out", Decimal("1"), date(2026, 11, 11))

    def test_history_sorted_by_date(self):
        self.book.record("t1", "in", Decimal("50"), date(2026, 11, 12))
        self.book.record("t1", "out", Decimal("5"), date(2026, 11, 10),
                         "early sale logged late")
        item, movs = self.book.history("t1")
        self.assertEqual(movs[0]["date"], "2026-11-10")
        self.assertEqual(movs[1]["date"], "2026-11-12")


class ReportTests(BookFixture):
    def setUp(self):
        super().setUp()
        self.book.record("t1", "in", Decimal("50"), date(2026, 11, 10))
        self.book.record("t1", "out", Decimal("35"), date(2026, 11, 15))
        # t1 now 15 <= level 20 -> LOW; t2 empty (0 <= 10) -> LOW

    def test_stock_rows(self):
        rows = {r["item"]["id"]: r for r in self.book.stock_rows()}
        self.assertEqual(rows["t1"]["qty"], Decimal("15"))
        self.assertTrue(rows["t1"]["low"])
        self.assertTrue(rows["t2"]["low"])

    def test_low_stock(self):
        low = self.book.low_stock()
        self.assertEqual({r["item"]["id"] for r in low}, {"t1", "t2"})

    def test_reorder_change_affects_low(self):
        self.book.set_reorder_level("t1", "10")
        low_ids = {r["item"]["id"] for r in self.book.low_stock()}
        self.assertNotIn("t1", low_ids)

    def test_export_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stock.csv"
            self.book.export_csv(out)
            with open(out, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["item_id", "item", "unit", "in_stock",
                                   "reorder_level", "low", "supplier"])
        t1 = [r for r in rows if r[0] == "t1"][0]
        self.assertEqual(t1[3], "15")
        self.assertEqual(t1[5], "LOW")
        self.assertEqual(t1[6], "Sharma Wholesale")

    def test_summary(self):
        s = self.book.summary()
        self.assertEqual(s["items"], 2)
        self.assertEqual(s["movements"], 2)
        self.assertEqual(s["low"], 2)


class PersistenceTests(BookFixture):
    def test_round_trip_and_lf_only(self):
        self.book.record("t1", "in", Decimal("50"), date(2026, 11, 10))
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "book.json"
            self.book.save(db)
            self.assertNotIn(b"\r\n", db.read_bytes())
            loaded = sb.StockBook.load(db)
        self.assertEqual(loaded.stock("t1"), Decimal("50"))
        self.assertEqual(loaded.summary(), self.book.summary())

    def test_failed_save_leaves_original_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "book.json"
            self.book.save(db)
            before = db.read_bytes()
            self.book.state["broken"] = object()
            try:
                with self.assertRaises(TypeError):
                    self.book.save(db)
                self.assertEqual(db.read_bytes(), before)
                leftovers = [p for p in Path(tmp).iterdir()
                             if p.name != "book.json"]
                self.assertEqual(leftovers, [])
            finally:
                del self.book.state["broken"]

    def test_corrupt_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bad.json"
            db.write_text("{oops", encoding="utf-8")
            with self.assertRaises(sb.StockBookError):
                sb.StockBook.load(db)

    def test_missing_file_gives_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = sb.StockBook.load(Path(tmp) / "none.json")
        self.assertEqual(loaded.summary()["items"], 0)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "cli.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sb.main(["--db", self.db] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_flow(self):
        code, out, _ = self.run_cli("add-item", "--name", "Parle-G Biscuits",
                                    "--unit", "packet", "--reorder", "20",
                                    "--supplier", "Sharma Wholesale")
        self.assertEqual(code, 0)
        self.assertIn("t1", out)
        code, out, _ = self.run_cli("in", "--item", "t1", "--qty", "50",
                                    "--date", "2026-11-10")
        self.assertIn("now 50", out)
        code, out, _ = self.run_cli("out", "--item", "t1", "--qty", "35",
                                    "--date", "2026-11-15",
                                    "--note", "weekly sales")
        self.assertIn("now 15", out)
        code, out, _ = self.run_cli("items")
        self.assertIn("LOW", out)
        self.assertIn("Sharma Wholesale", out)
        code, out, _ = self.run_cli("low")
        self.assertIn("Parle-G Biscuits", out)
        self.assertIn("15 packet left", out)
        code, out, _ = self.run_cli("history", "--item", "t1")
        self.assertIn("+50", out)
        self.assertIn("-35", out)
        code, out, _ = self.run_cli("set-reorder", "--item", "t1",
                                    "--level", "10")
        self.assertIn("10", out)
        code, out, _ = self.run_cli("low")
        self.assertIn("nothing below", out)
        code, out, _ = self.run_cli("summary")
        self.assertIn("items: 1", out)
        out_path = str(Path(self.tmp.name) / "stock.csv")
        code, _, _ = self.run_cli("export-csv", out_path)
        self.assertEqual(code, 0)
        with open(out_path, newline="", encoding="utf-8") as f:
            self.assertEqual(len(list(csv.reader(f))), 2)

    def test_negative_out_error(self):
        self.run_cli("add-item", "--name", "Rice 5kg")
        self.run_cli("in", "--item", "t1", "--qty", "5",
                     "--date", "2026-11-10")
        code, _, err = self.run_cli("out", "--item", "t1", "--qty", "6",
                                    "--date", "2026-11-11")
        self.assertEqual(code, 2)
        self.assertIn("only 5 in stock", err)

    def test_unknown_item_error(self):
        code, _, err = self.run_cli("in", "--item", "t99", "--qty", "1")
        self.assertEqual(code, 2)
        self.assertIn("no such item", err)


if __name__ == "__main__":
    unittest.main()
