# Text-to-SQL Analyst

Most text-to-SQL demos stop at "the model wrote a query". That is the easy half. The hard half is everything an analyst does without thinking: knowing the real column names, refusing to run a `DELETE`, noticing the query errored and fixing it, and then reading the result back in a sentence that doesn't overstate it.

This is a small, complete version of that loop over a synthetic payments database.

```
Q: Which merchant category had the highest refund rate in September 2026?

SELECT m.category, COUNT(*) AS payments, SUM(p.status = 'refunded') AS refunded,
       ROUND(100.0 * SUM(p.status = 'refunded') / COUNT(*), 2) AS refund_rate_pct
FROM payments p JOIN merchants m ON m.merchant_id = p.merchant_id
WHERE p.created_at >= '2026-09-01' AND p.created_at < '2026-10-01'
  AND p.status IN ('captured', 'refunded')
GROUP BY m.category ORDER BY refund_rate_pct DESC LIMIT 10

category       payments  refunded  refund_rate_pct
Travel            1,304       123             9.43
Fashion           1,319        96             7.28
Electronics       1,275        68             5.33
...
```

(With an API key, a checked one-sentence reading of the table follows.)

## How it works

1. **Schema in the prompt, with sample values.** Tables, columns, types and three example values per column are introspected from SQLite. The model never guesses a column name.
2. **Few-shot from `examples.json`.** Six hand-written question→SQL pairs. They're also what runs when no API key is set.
3. **Guardrails before execution.** One statement; must start with `SELECT`/`WITH`; no `PRAGMA`/`ATTACH`/writes; `LIMIT` enforced; the connection is opened read-only; a 5-second progress handler kills runaway queries.
4. **One repair.** If SQLite rejects the query, the error goes back to the model once with the failed SQL. If it fails again, it fails loudly.
5. **Reading, checked.** The one-sentence answer may only contain numbers present in the result table. If it doesn't, no sentence is printed. A table you can trust beats a sentence you can't.

## Run it

```bash
pip install -r requirements.txt           # numpy only; sqlite3 is stdlib
python ask.py --examples                  # works with no API key
python ask.py --schema
python ask.py "Which city's merchants have the worst UPI failure rate since August?"
```

Free-form questions need `GEMINI_API_KEY` (preferred, gemini-2.5-flash) or `ANTHROPIC_API_KEY` (claude-opus-5-5 via the official SDK). The database (`payments.db`, ~100k payments, Jan–Oct 2026) is built deterministically on first run.

## Data

Synthetic: 120 merchants across 8 categories, 6,000 customers, ~99k payments, ~3.5k refunds. Categories have different ticket sizes and refund/failure behaviour so the questions have real answers. No employer data.

Related writing: [Text-to-SQL with LLMs](https://nikunjkaushik.com/blog/text-to-sql-llms.html) · [LLMs for analytics](https://nikunjkaushik.com/blog/llms-for-analytics.html).
