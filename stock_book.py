#!/usr/bin/env python3
"""stock-book: zero-dependency stock and reorder record CLI for small shops.

Record what came in and what went out, and the book answers: how much of
each item is on the shelf right now, and what needs reordering (and from
whom) before it runs out.

Quantities only - no prices, no invoices, no live supplier feeds. Python
3.9+, standard library only. One JSON file, offline, no accounts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "stock_book.json")

UNITS = ("piece", "packet", "box", "kg", "g", "litre", "ml", "metre")


class StockBookError(Exception):
    """User-facing error (bad input, unknown id, ...)."""


def today_iso():
    return date.today().isoformat()


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise StockBookError("bad date %r (expected YYYY-MM-DD)" % text)


def parse_qty(text):
    try:
        qty = Decimal((text or "").strip())
    except InvalidOperation:
        raise StockBookError("bad quantity %r" % text)
    if qty <= 0:
        raise StockBookError("quantity must be positive")
    return qty


def parse_level(text):
    try:
        level = Decimal((text or "").strip())
    except InvalidOperation:
        raise StockBookError("bad reorder level %r" % text)
    if level < 0:
        raise StockBookError("reorder level must not be negative")
    return level


def fmt_qty(qty):
    """Decimal without trailing zeros: 2.500 -> 2.5, 10 -> 10."""
    s = format(qty.normalize(), "f")
    return s


class StockBook:
    def __init__(self):
        self.state = {"items": [], "movements": [], "next_item_id": 1}

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path):
        if not path.exists():
            return cls()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise StockBookError("could not parse %s: %s" % (path, exc))
        book = cls()
        for key in state:
            if key in book.state:
                book.state[key] = state[key]
        return book

    def save(self, path):
        data = json.dumps(self.state, indent=2, ensure_ascii=False) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- items ------------------------------------------------------------

    def add_item(self, name, unit="piece", reorder_level=Decimal(0), supplier="",
                 notes=""):
        name = (name or "").strip()
        if not name:
            raise StockBookError("item name must not be empty")
        if any(i["name"].lower() == name.lower() for i in self.state["items"]):
            raise StockBookError("item already exists: %s" % name)
        item = {"id": "t%d" % self.state["next_item_id"], "name": name,
                "unit": (unit or "piece").strip().lower(),
                "reorder_level": str(reorder_level),
                "supplier": (supplier or "").strip(),
                "notes": (notes or "").strip(), "added": today_iso()}
        self.state["next_item_id"] += 1
        self.state["items"].append(item)
        return item

    def get_item(self, ref):
        ref = (ref or "").strip()
        for i in self.state["items"]:
            if i["id"] == ref or i["name"].lower() == ref.lower():
                return i
        raise StockBookError("no such item: %s" % ref)

    def set_reorder_level(self, item_ref, level):
        item = self.get_item(item_ref)
        item["reorder_level"] = str(level)
        return item

    # -- movements --------------------------------------------------------

    def stock(self, item_id):
        total = Decimal(0)
        for m in self.state["movements"]:
            if m["item_id"] != item_id:
                continue
            qty = Decimal(m["qty"])
            total += qty if m["kind"] == "in" else -qty
        return total

    def record(self, item_ref, kind, qty, when, note=""):
        item = self.get_item(item_ref)
        if kind not in ("in", "out"):
            raise StockBookError("kind must be 'in' or 'out'")
        if kind == "out":
            available = self.stock(item["id"])
            if qty > available:
                raise StockBookError(
                    "cannot take out %s %s of %s: only %s in stock"
                    % (fmt_qty(qty), item["unit"], item["name"],
                       fmt_qty(available)))
        mov = {"item_id": item["id"], "kind": kind, "qty": str(qty),
               "date": when.isoformat(), "note": (note or "").strip()}
        self.state["movements"].append(mov)
        return mov

    def history(self, item_ref):
        item = self.get_item(item_ref)
        movs = [m for m in self.state["movements"]
                if m["item_id"] == item["id"]]
        movs.sort(key=lambda m: m["date"])
        return item, movs

    # -- reports ----------------------------------------------------------

    def stock_rows(self):
        rows = []
        for i in self.state["items"]:
            level = Decimal(i["reorder_level"])
            qty = self.stock(i["id"])
            rows.append({"item": i, "qty": qty, "level": level,
                         "low": qty <= level})
        return rows

    def low_stock(self):
        return [r for r in self.stock_rows() if r["low"]]

    def export_csv(self, out_path):
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["item_id", "item", "unit", "in_stock",
                        "reorder_level", "low", "supplier"])
            for r in self.stock_rows():
                i = r["item"]
                w.writerow([i["id"], i["name"], i["unit"], fmt_qty(r["qty"]),
                            fmt_qty(r["level"]),
                            "LOW" if r["low"] else "", i["supplier"]])

    def summary(self):
        rows = self.stock_rows()
        return {"items": len(rows),
                "movements": len(self.state["movements"]),
                "low": sum(1 for r in rows if r["low"])}


# -- CLI --------------------------------------------------------------------

def cmd_add_item(book, args):
    item = book.add_item(args.name, args.unit,
                         parse_level(args.reorder) if args.reorder
                         else Decimal(0),
                         args.supplier, args.notes)
    print("added item %s: %s (%s)" % (item["id"], item["name"],
                                      item["unit"]))


def cmd_items(book, args):
    rows = book.stock_rows()
    if not rows:
        print("no items yet")
        return
    for r in rows:
        i = r["item"]
        low = "  LOW" if r["low"] else ""
        sup = ("  from %s" % i["supplier"]) if i["supplier"] else ""
        print("%-4s  %-24s  %8s %-7s (reorder at %s)%s%s"
              % (i["id"], i["name"], fmt_qty(r["qty"]), i["unit"],
                 fmt_qty(r["level"]), low, sup))


def cmd_in(book, args):
    mov = book.record(args.item, "in", parse_qty(args.qty),
                      parse_date(args.date) if args.date else date.today(),
                      args.note)
    item = book.get_item(args.item)
    print("in: +%s %s of %s (now %s)"
          % (fmt_qty(Decimal(mov["qty"])), item["unit"], item["name"],
             fmt_qty(book.stock(item["id"]))))


def cmd_out(book, args):
    mov = book.record(args.item, "out", parse_qty(args.qty),
                      parse_date(args.date) if args.date else date.today(),
                      args.note)
    item = book.get_item(args.item)
    print("out: -%s %s of %s (now %s)"
          % (fmt_qty(Decimal(mov["qty"])), item["unit"], item["name"],
             fmt_qty(book.stock(item["id"]))))


def cmd_reorder(book, args):
    item = book.set_reorder_level(args.item, parse_level(args.level))
    print("%s: reorder level set to %s %s"
          % (item["name"], fmt_qty(Decimal(item["reorder_level"])),
             item["unit"]))


def cmd_low(book, args):
    rows = book.low_stock()
    if not rows:
        print("nothing below its reorder level")
        return
    print("reorder these:")
    for r in rows:
        i = r["item"]
        sup = (" from %s" % i["supplier"]) if i["supplier"] else ""
        print("  %-24s  %s %s left (reorder at %s)%s"
              % (i["name"], fmt_qty(r["qty"]), i["unit"],
                 fmt_qty(r["level"]), sup))


def cmd_history(book, args):
    item, movs = book.history(args.item)
    print("%s (%s) - %d movement(s), %s %s in stock"
          % (item["name"], item["unit"], len(movs),
             fmt_qty(book.stock(item["id"])), item["unit"]))
    for m in movs:
        sign = "+" if m["kind"] == "in" else "-"
        note = ("  - %s" % m["note"]) if m["note"] else ""
        print("  %s  %s%s%s" % (m["date"], sign, fmt_qty(Decimal(m["qty"])),
                                note))


def cmd_export_csv(book, args):
    book.export_csv(args.path)
    print("wrote %s" % args.path)


def cmd_summary(book, args):
    s = book.summary()
    print("items: %d   movements: %d   below reorder level: %d"
          % (s["items"], s["movements"], s["low"]))


def build_parser():
    p = argparse.ArgumentParser(
        prog="stock-book",
        description="Stock and reorder record for small shops. Quantities "
                    "only - no prices, no live supplier data. Offline, one "
                    "JSON file.")
    p.add_argument("--db", default=DEFAULT_DB,
                   help="store file (default: stock_book.json next to this "
                        "script)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("add-item", help="register an item")
    sp.add_argument("--name", required=True)
    sp.add_argument("--unit", default="piece",
                    help="piece/packet/box/kg/g/litre/ml/metre (default: "
                         "piece)")
    sp.add_argument("--reorder", default=None, help="reorder level")
    sp.add_argument("--supplier", default="", help="usual supplier name")
    sp.add_argument("--notes", default="")
    sp.set_defaults(func=cmd_add_item)

    sp = sub.add_parser("items", help="list items with current stock")
    sp.set_defaults(func=cmd_items)

    sp = sub.add_parser("in", help="record stock coming in")
    sp.add_argument("--item", required=True, help="id or exact name")
    sp.add_argument("--qty", required=True)
    sp.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_in)

    sp = sub.add_parser("out", help="record stock going out (sale/wastage)")
    sp.add_argument("--item", required=True, help="id or exact name")
    sp.add_argument("--qty", required=True)
    sp.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_out)

    sp = sub.add_parser("set-reorder", help="change an item's reorder level")
    sp.add_argument("--item", required=True)
    sp.add_argument("--level", required=True)
    sp.set_defaults(func=cmd_reorder)

    sp = sub.add_parser("low", help="what needs reordering")
    sp.set_defaults(func=cmd_low)

    sp = sub.add_parser("history", help="movement history for one item")
    sp.add_argument("--item", required=True)
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("export-csv", help="export the stock table to CSV")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_export_csv)

    sp = sub.add_parser("summary", help="one-line store summary")
    sp.set_defaults(func=cmd_summary)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    from pathlib import Path
    db = Path(args.db)
    try:
        book = StockBook.load(db)
        args.func(book, args)
        book.save(db)
    except StockBookError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
