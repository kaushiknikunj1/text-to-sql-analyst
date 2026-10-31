#!/usr/bin/env python3
"""Text-to-SQL Analyst - plain-English questions over a payments database, with guardrails.

    python ask.py "Which merchant category had the highest refund rate in September 2026?"
    python ask.py --examples          # run the bundled questions (works with no API key)
    python ask.py --schema            # print the schema the model sees

How it works
  1. The schema (tables, columns, a few sample values) is introspected from SQLite and
     put in the prompt - the model never guesses column names.
  2. The LLM writes one SQLite SELECT. Guardrails: single statement, SELECT/WITH only,
     no PRAGMA/ATTACH/writes, LIMIT enforced, read-only connection, 5-second timeout.
  3. The query runs. If SQLite rejects it, the error goes back to the model for ONE repair.
  4. The result table is printed, then a one-line reading of it. The reading may only use
     numbers that appear in the result - checked, not trusted.

Without GEMINI_API_KEY / ANTHROPIC_API_KEY the bundled examples still run - their SQL is
hand-written and stored in examples.json, which is also what the model is shown as few-shot.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import llm  # noqa: E402
from build_db import DB_PATH, build  # noqa: E402

EXAMPLES = json.loads((Path(__file__).parent / "examples.json").read_text())
MAX_ROWS = 50


# ----------------------------------------------------------------------------- schema
def schema(conn: sqlite3.Connection) -> str:
    lines = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
        lines.append(f"TABLE {name} (")
        for _, col, typ, *_ in cols:
            sample = conn.execute(f"SELECT DISTINCT {col} FROM {name} WHERE {col} IS NOT NULL LIMIT 3").fetchall()
            samp = ", ".join(str(s[0]) for s in sample)
            lines.append(f"  {col} {typ}   -- e.g. {samp}")
        lines.append(")")
    lines.append("-- Dates are ISO text (YYYY-MM-DD HH:MM:SS). Amounts are INR. "
                 "payments.status in ('captured','failed','refunded').")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- guardrails
FORBIDDEN = re.compile(r"\b(PRAGMA|ATTACH|DETACH|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|VACUUM)\b", re.I)


def clean_sql(sql: str) -> str:
    sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", sql.strip(), flags=re.I | re.M).strip().rstrip(";")
    if ";" in sql:
        raise ValueError("one statement only")
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I):
        raise ValueError("must start with SELECT or WITH")
    if FORBIDDEN.search(sql):
        raise ValueError("write/admin statements are not allowed")
    if not re.search(r"\bLIMIT\s+\d+", sql, re.I):
        sql += f"\nLIMIT {MAX_ROWS}"
    return sql


def run_sql(sql: str):
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    deadline = time.time() + 5
    conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10_000)
    try:
        cur = conn.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchmany(MAX_ROWS)
    finally:
        conn.close()
    return cols, rows


# ----------------------------------------------------------------------------- model
SQL_SYSTEM = """You translate analytics questions into ONE SQLite SELECT statement.
Rules: use only tables and columns in the schema; SQLite dialect (strftime for dates, no DATE_TRUNC);
return only the SQL, no prose, no markdown; always alias computed columns; prefer ROUND(x, 2) for rates;
"last month" style phrases are relative to 2026-10-31."""

READ_SYSTEM = """Given a question and a small result table, write ONE sentence that answers the question
for a business reader. Use only numbers that appear in the table. No speculation."""


def fewshot() -> str:
    return "\n\n".join(f"Q: {e['question']}\nSQL:\n{e['sql']}" for e in EXAMPLES[:4])


def ask_model(question: str, sch: str, error: str | None = None, prev: str | None = None) -> str | None:
    user = f"SCHEMA\n{sch}\n\nEXAMPLES\n{fewshot()}\n\nQ: {question}\nSQL:"
    if error:
        user += f"\n\nYour previous SQL failed.\nSQL:\n{prev}\nSQLite error: {error}\nWrite a corrected statement."
    return llm.complete(SQL_SYSTEM, user, max_tokens=400)


def read_result(question: str, cols, rows) -> str | None:
    table = {"columns": cols, "rows": rows[:10]}
    text = llm.complete(READ_SYSTEM, f"Question: {question}\nResult: {json.dumps(table, default=str)}", 120)
    if text and not llm.unpinned(text, table):
        return text
    return None


# ----------------------------------------------------------------------------- flow
def answer(question: str, sch: str) -> tuple[str, str]:
    """Returns (sql, source)."""
    if llm.provider() == "none":
        match = difflib.get_close_matches(question.lower(), [e["question"].lower() for e in EXAMPLES], 1, 0.6)
        if match:
            ex = next(e for e in EXAMPLES if e["question"].lower() == match[0])
            return ex["sql"], "bundled example (no API key)"
        raise SystemExit("No API key set and this question is not one of the bundled examples.\n"
                         "Set GEMINI_API_KEY or ANTHROPIC_API_KEY, or run with --examples.")
    sql = clean_sql(ask_model(question, sch) or "")
    try:
        run_sql(sql)
        return sql, f"llm ({llm.provider()})"
    except sqlite3.Error as e:
        repaired = clean_sql(ask_model(question, sch, str(e), sql) or "")
        run_sql(repaired)  # raise if still broken
        return repaired, f"llm ({llm.provider()}), repaired once"


def print_table(cols, rows):
    rows = [[("" if v is None else v) for v in r] for r in rows]
    widths = [max(len(str(c)), *(len(f"{r[i]:,}" if isinstance(r[i], (int, float)) else str(r[i])) for r in rows)) if rows else len(str(c))
              for i, c in enumerate(cols)]
    fmt_cell = lambda v, w: (f"{v:,}" if isinstance(v, (int, float)) else str(v)).rjust(w) if isinstance(v, (int, float)) else str(v).ljust(w)
    print("  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(fmt_cell(v, w) for v, w in zip(r, widths)))
    if not rows:
        print("(no rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--examples", action="store_true")
    ap.add_argument("--schema", action="store_true")
    a = ap.parse_args()

    if not DB_PATH.exists():
        build()
    conn = sqlite3.connect(DB_PATH)
    sch = schema(conn)
    conn.close()

    if a.schema:
        print(sch); return
    questions = [e["question"] for e in EXAMPLES] if a.examples else [a.question]
    if not questions[0]:
        ap.error("give a question, or --examples")

    print(f"LLM provider: {llm.provider()}\n")
    for q in questions:
        print(f"Q: {q}")
        sql, src = answer(q, sch)
        print(f"\n{sql}\n   -- {src}\n")
        cols, rows = run_sql(sql)
        print_table(cols, rows)
        reading = read_result(q, cols, rows) if llm.provider() != "none" else None
        if reading:
            print(f"\n→ {reading}")
        print("\n" + "─" * 72 + "\n")


if __name__ == "__main__":
    main()
