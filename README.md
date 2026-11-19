# stock-book

A zero-dependency command-line stock and reorder record for small shops.
Record what came in and what went out, and the book answers: how much of
each item is on the shelf right now, and what needs reordering - and from
whom - before it runs out.

Single Python file, standard library only (Python 3.9+). Fully offline; all
data lives in one local JSON file you own.

## Why this exists

On Razorpay's public "Fix My Itch" list, one entry asks: "Why can't shops
see real-time supplier stock levels?" (page fetched and entry re-verified on
2026-11-19; https://razorpay.com/m/fix-my-itch/). That list is curated by
Razorpay - it shows the problem was submitted and published, not how common
it is.

This tool cannot show a supplier's stock - that needs the supplier to share
data, and no offline tool can do it. What a shop *can* control is knowing
its **own** shelf: current quantity per item, and a reorder level per item
with the usual supplier's name attached, so the "what do I need to order and
from whom" list is one command away instead of a walk around the shop.

## What it does

- **Item registry** - name, unit (piece / packet / box / kg / g / litre /
  ml / metre, or your own word), reorder level, usual supplier, notes.
- **Movements** - `in` (purchase/return) and `out` (sale/wastage), each
  with a date and note. Decimal quantities work (`2.5 kg`).
- **Negative stock rejected** - taking out more than you have is an error,
  not a silent minus.
- **Low-stock list** - every item at or below its reorder level, with the
  supplier's name - the reorder list.
- **History** - every movement for one item, oldest first.
- **CSV export** of the stock table.

## What it does NOT do (limits)

- **Quantities only.** No purchase prices, sale prices, margins, or
  invoices (see the sibling tools kitchen-cost-book and invoice-ledger for
  money-side tracking).
- **No live supplier data.** It cannot see anyone else's stock, prices, or
  delivery times; the supplier name is just text you record.
- **Manual entry.** No barcode scanning, no billing integration - stock is
  right only if movements are recorded.
- **Not multi-shop.** One file, one shop, one person at a time.
- **Durability:** saves are atomic (write to a temp file, then replace the
  store), and a failed save leaves the previous store intact - that behavior
  is covered by tests. Beyond that there are no guarantees against power
  loss mid-write or two people editing the same file at once.

## Landscape note (checked 2026-11-19)

Dated searches on 2026-11-19 for `simple stock inventory tracker cli open
source` and `small shop inventory reorder level software` surfaced both
existing open-source CLI inventory trackers and hosted inventory SaaS with
reorder-point features. So CLI stock trackers exist. This project's scope is
a small shop's reorder workflow specifically - per-item reorder levels with
the usual supplier attached, negative-stock prevention, and a one-command
reorder list - in a single dependency-free Python file. It is not a claim
that no similar tool exists.

## Install and quick start

Requires Python 3.9+. No dependencies.

```bash
python3 stock_book.py --help
```

Walkthrough (each command saves automatically to `--db`, default
`stock_book.json`):

```bash
python3 stock_book.py add-item --name "Parle-G Biscuits" --unit packet \
  --reorder 20 --supplier "Sharma Wholesale"

python3 stock_book.py in  --item t1 --qty 50 --date 2026-11-10 --note "weekly purchase"
python3 stock_book.py out --item t1 --qty 35 --date 2026-11-15 --note "weekly sales"

python3 stock_book.py items     # everything, with LOW markers
python3 stock_book.py low       # the reorder list, with supplier names
python3 stock_book.py history --item t1
python3 stock_book.py set-reorder --item t1 --level 10
python3 stock_book.py export-csv stock.csv
```

Example `low` output:

```
reorder these:
  Parle-G Biscuits          15 packet left (reorder at 20) from Sharma Wholesale
```

Items can be referred to by id (`t1`) or by exact name. `--date` defaults
to today.

## Data and tests

- Store: one JSON file (`--db`, default `stock_book.json` next to the
  script), written with LF line endings and atomic replace. A failed
  serialization leaves the previous file untouched (tested).
- Quantities are Decimal (no float drift); zero and negative movement
  quantities are rejected, and `out` movements cannot exceed stock on hand.
- Tests: 28 unit tests (`python3 -m unittest -v`) covering quantity/level
  parsing, item registry, stock math with decimals, negative-stock
  rejection, low-stock and reorder-level behavior, history ordering, CSV
  export, persistence, and the CLI. CI runs them on ubuntu-24.04 and
  windows-latest, Python 3.9 and 3.13.

## License

MIT - see [LICENSE](LICENSE). Copyright (c) 2026 Mehul Jhabak.
