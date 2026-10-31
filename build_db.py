"""Build the synthetic payments database (SQLite) used by ask.py. Deterministic."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).parent / "payments.db"
RNG = np.random.default_rng(11)

CATEGORIES = ["Food delivery", "Fashion", "Electronics", "Travel", "Education", "Gaming", "Utilities", "Healthcare"]
CITIES = ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Pune", "Chennai", "Kolkata", "Jaipur"]
METHODS = ["upi", "card", "netbanking", "wallet"]
CHANNELS = ["paid_search", "paid_social", "referral", "organic", "partner"]


def build() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE merchants (merchant_id INTEGER PRIMARY KEY, name TEXT, category TEXT, city TEXT, onboarded_at TEXT, plan TEXT);
    CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, city TEXT, acquisition_channel TEXT, signed_up_at TEXT);
    CREATE TABLE payments (payment_id INTEGER PRIMARY KEY, merchant_id INTEGER, customer_id INTEGER, amount_inr REAL,
                           method TEXT, status TEXT, created_at TEXT, fee_inr REAL);
    CREATE TABLE refunds (refund_id INTEGER PRIMARY KEY, payment_id INTEGER, amount_inr REAL, reason TEXT, created_at TEXT);
    """)

    n_m, n_c = 120, 6000
    start = datetime(2026, 1, 1)
    merchants = []
    for i in range(1, n_m + 1):
        cat = CATEGORIES[(i * 7) % len(CATEGORIES)]
        merchants.append((i, f"{cat.split()[0]} Co {i}", cat, CITIES[i % len(CITIES)],
                          (start + timedelta(days=int(RNG.integers(0, 240)))).strftime("%Y-%m-%d"),
                          RNG.choice(["starter", "growth", "enterprise"], p=[.55, .35, .10])))
    c.executemany("INSERT INTO merchants VALUES (?,?,?,?,?,?)", merchants)

    customers = [(i, CITIES[int(RNG.integers(0, len(CITIES)))], str(RNG.choice(CHANNELS, p=[.3, .25, .15, .2, .1])),
                  (start + timedelta(days=int(RNG.integers(0, 290)))).strftime("%Y-%m-%d"))
                 for i in range(1, n_c + 1)]
    c.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)

    # category effects: travel = big tickets + more refunds; gaming = many small, more failures
    cat_amount = {"Food delivery": 380, "Fashion": 1900, "Electronics": 7400, "Travel": 9800,
                  "Education": 3200, "Gaming": 240, "Utilities": 1100, "Healthcare": 1500}
    cat_refund = {"Travel": .09, "Fashion": .07, "Electronics": .05, "Gaming": .02, "Food delivery": .03,
                  "Education": .02, "Utilities": .005, "Healthcare": .015}
    cat_fail = {"Gaming": .11, "Travel": .06}
    pid = rid = 0
    pay, ref = [], []
    for day in range(0, 304):                     # Jan 1 -> Oct 31, 2026
        dt = start + timedelta(days=day)
        growth = 1 + day / 304 * 0.6
        n_today = int(RNG.poisson(260 * growth * (0.85 if dt.weekday() >= 5 else 1.0)))
        for _ in range(n_today):
            m = merchants[int(RNG.integers(0, n_m))]
            cat = m[2]
            amt = float(np.round(RNG.lognormal(np.log(cat_amount[cat]), 0.5), 2))
            method = str(RNG.choice(METHODS, p=[.52, .3, .1, .08]))
            fail_p = cat_fail.get(cat, .04) + (.02 if method == "netbanking" else 0)
            status = "failed" if RNG.random() < fail_p else "captured"
            ts = dt + timedelta(seconds=int(RNG.integers(0, 86400)))
            pid += 1
            fee = round(amt * (0.0 if method == "upi" else 0.02), 2) if status == "captured" else 0.0
            pay.append((pid, m[0], int(RNG.integers(1, n_c + 1)), amt, method, status, ts.strftime("%Y-%m-%d %H:%M:%S"), fee))
            if status == "captured" and RNG.random() < cat_refund[cat]:
                rid += 1
                rts = ts + timedelta(days=int(RNG.integers(1, 12)))
                ref.append((rid, pid, round(amt * float(RNG.choice([1.0, 1.0, 0.5])), 2),
                            str(RNG.choice(["customer_request", "duplicate", "not_delivered", "quality"])),
                            rts.strftime("%Y-%m-%d %H:%M:%S")))
                pay[-1] = pay[-1][:5] + ("refunded",) + pay[-1][6:]
    c.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?)", pay)
    c.executemany("INSERT INTO refunds VALUES (?,?,?,?,?)", ref)
    c.executescript("CREATE INDEX ix_pay_m ON payments(merchant_id); CREATE INDEX ix_pay_t ON payments(created_at);")
    conn.commit()
    conn.close()
    print(f"built {DB_PATH.name}: {n_m} merchants, {n_c} customers, {len(pay):,} payments, {len(ref):,} refunds")


if __name__ == "__main__":
    build()
