# SQL Calculation Agent: Report

Agentic Software Engineering, Part 1, Assignment 1. The submitted model is
**`qwen2.5:3b`**, served locally by Ollama. No hosted API is used.

## 1. Architecture

```text
question
   |
   v
agent loop (JSON decisions, maximum 6 steps, 6000-token budget)
   |
   +--> SQL-generation prompt with the complete schema
   |       |
   |       +--> deterministic query for explicit customer/month spending
   |       |
   |       '--> qwen2.5:3b via Ollama /api/chat
   |
   +--> read-only SQLite executor
   |       |
   |       '--> rows or an explicit SQL error
   |
   '--> final grounded answer, status, timing, and conversation log
```

### Database

[`build_database.py`](<F:/books/7th sem/Agentic SE L/SQL calculation agent/build_database.py>)
creates the deterministic SQLite database at
[`data/sales.db`](<F:/books/7th sem/Agentic SE L/SQL calculation agent/data/sales.db>).
It contains four related tables:

- `customers`
- `products`
- `orders`
- `order_items`

Revenue is derived as `SUM(order_items.quantity * order_items.unit_price)`.
Completed revenue additionally filters `orders.status = 'completed'`.

### Agent loop

[`agent.py`](<F:/books/7th sem/Agentic SE L/SQL calculation agent/agent.py>)
uses Ollama's `/api/chat` endpoint and requires JSON decisions. The model either
requests SQL generation or provides a final answer. SQL-generation prompts
include the schema, business definitions, and any previous executor error so
that invalid SQL can be corrected.

Questions matching the explicit customer/month spending pattern use a
deterministic parameterized-by-construction query generator instead of an
additional model call. This prevents the small model from incorrectly deciding
that `orders` and `order_items` cannot represent spending.

### Read-only safety

The executor:

1. accepts only one statement beginning with `SELECT` or `WITH`;
2. rejects comments, multiple statements, write operations, DDL, and
   connection-changing operations;
3. opens SQLite using `mode=ro`; and
4. limits returned rows to 100.

SQL errors are returned to the agent as structured results, allowing another
SQL-generation attempt within the step cap.

## 2. Evaluation

The evaluation set contains 17 questions and three trials per question. It
covers simple counts, joins, grouping, calculations, date filtering, and
unsupported questions.

The expected answers are listed in
[`questions.json`](<F:/books/7th sem/Agentic SE L/SQL calculation agent/questions.json>).
The recorded evaluation summary is in
[`artifacts/evaluation_report.md`](<F:/books/7th sem/Agentic SE L/SQL calculation agent/artifacts/evaluation_report.md>).

| Metric | Result |
|---|---:|
| Questions | 17 |
| Trials per question | 3 |
| Total trials | 51 |
| pass@1 | 11/17 (64.7%) |
| pass^3 | 10/17 (58.8%) |

These metrics are based on the recorded `status` field. A status of `ok` does
not by itself prove that the numerical answer is correct; the evaluation
artifact explicitly records an example where a 0% cancelled-order answer was
marked `ok` despite an expected value of 25.0%.

## 3. Failure analysis

### Schema and unsupported-question misclassification

The small model sometimes expects a table named `sales` or `transactions` and
rejects questions that are answerable from `orders` and `order_items`. This
occurred for the customer spending question even though spending is derivable
from the available schema.

### Invalid SQL

Some generated queries contain SQLite syntax errors. For example, the
month-over-month revenue query generated `SUM oi.quantity * oi.unit_price`
instead of the valid `SUM(oi.quantity * oi.unit_price)` expression.

### Local model timeout

At least one trial failed because the local Ollama request timed out. This is a
runtime performance issue rather than a database or SQL-safety failure.

### Incorrect calculations

The model can produce a syntactically valid but logically incorrect aggregate,
especially for questions requiring multiple aggregation levels. The current
recorded evaluation measures status and does not fully prevent this class of
error.

## 4. Improvement: SQL correction retries

The agent was improved so that executor errors are included in the next
SQL-generation prompt. Instead of ending after the first invalid query, the
agent can retry until it reaches the six-step cap or the 6000-token budget.

| Capability | Earlier behavior | Current behavior |
|---|---:|---:|
| SQL correction retries after an executor error | 0 | Up to 5 additional steps |
| Maximum agent steps | Immediate failure path | 6 |
| SQL result row limit | Not applicable | 100 rows |

The available artifacts do not contain a baseline run from before this change,
so a before/after pass-count improvement cannot be independently verified.

## 5. Reproduction

From PowerShell:

```powershell
Start-Process ollama -ArgumentList "serve"
.\.venv\Scripts\python.exe build_database.py
ollama pull qwen2.5:3b
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe agent.py
```

For a single question:

```powershell
.\.venv\Scripts\python.exe agent.py "How many customers are there?"
```

Conversation records are appended to
[`artifacts/conversation_log.jsonl`](<F:/books/7th sem/Agentic SE L/SQL calculation agent/artifacts/conversation_log.jsonl>).

## 6. Limitations and future work

- Add an evaluation checker that compares numerical answers against expected
  values instead of relying primarily on status.
- Add a held-out question set to reduce evaluation bias.
- Add a grounding check that rejects numbers not present in the SQL result.
- Add explicit guards for repeated SQL calls and unsupported concepts such as
  profit or returns.
- Compare `qwen2.5:3b` with a larger local model using the same questions,
  trials, and scoring procedure.
