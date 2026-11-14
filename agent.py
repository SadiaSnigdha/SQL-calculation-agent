
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from build_database import DATABASE_PATH

MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MAX_ROWS = 100
CONVERSATION_LOG_PATH = Path(__file__).resolve().parent / "artifacts" / "conversation_log.jsonl"
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
SCHEMA = """customers(customer_id, name, region, signup_date)
products(product_id, name, category, unit_price)
orders(order_id, customer_id, order_date, status)
order_items(order_item_id, order_id, product_id, quantity, unit_price)
Relationships: orders.customer_id=customers.customer_id;
order_items.order_id=orders.order_id; order_items.product_id=products.product_id.
Business meanings: revenue is SUM(order_items.quantity * order_items.unit_price);
completed revenue filters orders.status='completed'; order_date is the order date
and uses ISO text such as '2025-01-%'; cancelled orders have status='cancelled'.
Use 100.0 rather than 100 for percentage calculations to avoid integer division.
An unsupported question asks for data that cannot be derived from these columns.
Do not call a question unsupported merely because a table is not named 'sales' or
'transactions'; derive sales and revenue from orders and order_items."""


class OllamaError(RuntimeError):
    pass


def ollama_chat(messages: list[dict[str, str]], *, json_mode: bool = True) -> tuple[str, dict[str, int]]:
    payload: dict[str, Any] = {"model": MODEL, "messages": messages, "stream": False}
    if json_mode:
        payload["format"] = "json"
    request = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc
    message = data.get("message", {})
    text = message.get("content")
    if not isinstance(text, str):
        raise OllamaError(f"Ollama returned no message content: {data}")
    return text, {
        "prompt": int(data.get("prompt_eval_count", 0) or 0),
        "completion": int(data.get("eval_count", 0) or 0),
    }


def sql_executor(sql: str, db_path: Path = DATABASE_PATH, row_limit: int = MAX_ROWS) -> dict[str, Any]:
    """Execute one safe SELECT and return column names and rows or a clear error."""
    if isinstance(row_limit, bool) or not isinstance(row_limit, int) or row_limit < 1:
        return {"ok": False, "error": "row_limit must be a positive integer"}
    statement = sql.strip()
    if not statement:
        return {"ok": False, "error": "SQL is empty"}
    if ";" in statement[:-1] or "--" in statement or "/*" in statement:
        return {"ok": False, "error": "Only one SQL statement without comments is allowed"}
    if not re.match(r"^(SELECT|WITH)\b", statement, re.IGNORECASE):
        return {"ok": False, "error": "Read-only executor accepts only SELECT or WITH queries"}
    if re.search(r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|ATTACH|DETACH|PRAGMA|REPLACE|VACUUM|CREATE)\b", statement, re.IGNORECASE):
        return {"ok": False, "error": "Write, DDL, and connection-changing SQL is not allowed"}
    limited = f"SELECT * FROM ({statement.rstrip(';')}) AS agent_result LIMIT {int(row_limit)}"
    try:
        connection = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            cursor = connection.execute(limited)
            columns = [description[0] for description in cursor.description or ()]
            rows = [list(row) for row in cursor.fetchall()]
        finally:
            connection.close()
        return {"ok": True, "columns": columns, "rows": rows, "row_limit": row_limit}
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"SQL error: {exc}"}


def deterministic_spending_query(question: str) -> str | None:
    """Build a safe aggregate query for explicit customer/month spending questions."""
    match = re.fullmatch(
        r"\s*how much did customer\s+(\d+)\s+spend in\s+"
        r"(january|february|march|april|may|june|july|august|september|"
        r"october|november|december)\s+(\d{4})\s*\??\s*",
        question,
        re.IGNORECASE,
    )
    if not match:
        return None
    customer_id, month_name, year = match.groups()
    month = MONTHS[month_name.lower()]
    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{int(year) + 1}-01-01"
    else:
        end_date = f"{year}-{month + 1:02d}-01"
    return (
        "SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0.0) AS total_spent "
        "FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.order_id "
        f"WHERE o.customer_id = {customer_id} "
        f"AND o.order_date >= '{start_date}' AND o.order_date < '{end_date}'"
    )


def sql_generation_tool(question: str, correction: str = "") -> tuple[str, dict[str, int]]:
    direct_query = deterministic_spending_query(question)
    if direct_query is not None:
        return direct_query, {"prompt": 0, "completion": 0}
    prompt = (
        "You generate SQL only; do not answer the question. Return JSON exactly "
        '{"sql":"SELECT ..."} or {"unsupported":"brief reason"}.\n'
        "Prefer a single aggregate SELECT that directly answers the question. "
        "Use only the supplied tables and columns. Questions about revenue, "
        "spending, order dates, or completed orders are answerable using the "
        "business meanings below. Mark unsupported only when the requested fact "
        "cannot be derived from the schema; never invent a table or column.\n"
        f"Schema:\n{SCHEMA}\nQuestion: {question}\n"
        f"Previous executor error (fix it if present): {correction}"
    )
    text, usage = ollama_chat(
        [{"role": "system", "content": "Generate one SQLite SELECT query using the supplied schema."},
         {"role": "user", "content": prompt}]
    )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"SQL generation was not JSON: {text}") from exc
    if "unsupported" in data:
        return "", usage | {"unsupported": str(data["unsupported"])}
    sql = data.get("sql")
    if not isinstance(sql, str):
        raise OllamaError(f"SQL generation omitted sql: {data}")
    return sql, usage


@dataclass
class RunResult:
    question: str
    answer: str = ""
    status: str = "error"
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=lambda: {"prompt": 0, "completion": 0})
    elapsed_ms: int = 0


def log_interaction(result: RunResult, log_path: Path = CONVERSATION_LOG_PATH) -> None:
    """Append one user question and agent response to the conversation log."""
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "question": result.question,
        "answer": result.answer,
        "status": result.status,
        "elapsed_ms": result.elapsed_ms,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=True) + "\n")


def run_agent(question: str, step_cap: int = 6, token_cap: int = 6000) -> RunResult:
    result = RunResult(question=question)
    started = time.perf_counter()
    history: list[dict[str, str]] = [
        {"role": "system", "content": (
            "You are a SQL calculation agent. Choose a tool by returning JSON exactly: "
            '{"tool":"sql_generation","question":"..."} or '
            '{"final":"answer grounded in the latest rows"} or '
            '{"final":"I cannot answer this from the database because ..."}.\n'
            "Use sql_generation before every answerable question. Do not invent values. "
            "After the tool returns rows, answer only from those rows; if execution "
            "fails, request a corrected SQL query instead of answering."
        )},
        {"role": "user", "content": question},
    ]
    correction = ""
    for step in range(1, step_cap + 1):
        result.steps = step
        try:
            text, usage = ollama_chat(history)
            for key in result.tokens:
                result.tokens[key] += usage[key]
            if sum(result.tokens.values()) > token_cap:
                result.status, result.answer = "token_cap", "Stopped because the token budget was reached."
                break
            decision = json.loads(text)
            if not isinstance(decision, dict) or not decision:
                history.append({"role": "assistant", "content": text})
                history.append({
                    "role": "user",
                    "content": (
                        "Your previous response was empty or invalid. Return exactly "
                        '{"final":"..."} using the latest SQL rows, or '
                        '{"tool":"sql_generation","question":"..."} if another query is needed.'
                    ),
                })
                continue
            if "final" in decision:
                if result.tool_calls:
                    result.answer = str(decision["final"])
                    result.status = "ok" if result.answer else "error"
                    break
                decision = {"tool": "sql_generation", "question": question}
            if decision.get("tool") != "sql_generation":
                raise OllamaError(f"Unknown tool decision: {decision}")
            generated_sql, generation_usage = sql_generation_tool(str(decision.get("question", question)), correction)
            for key in result.tokens:
                result.tokens[key] += generation_usage[key]
            if sum(result.tokens.values()) > token_cap:
                result.status, result.answer = "token_cap", "Stopped because the token budget was reached."
                break
            if "unsupported" in generation_usage:
                result.answer = f"I cannot answer this from the database because {generation_usage['unsupported']}."
                result.status = "ok"
                result.tool_calls.append({"tool": "sql_generation", "question": question, "unsupported": True})
                break
            result.sql.append(generated_sql)
            execution = sql_executor(generated_sql)
            result.tool_calls.append({"tool": "sql_generation", "sql": generated_sql, "result": execution})
            history.append({"role": "assistant", "content": text})
            history.append({"role": "tool", "content": json.dumps(execution)})
            correction = execution.get("error", "")
        except (OllamaError, json.JSONDecodeError, TypeError, ValueError) as exc:
            result.status, result.answer = "error", str(exc)
            break
    else:
        result.status, result.answer = "step_cap", "Stopped after the step cap."
    if result.steps >= step_cap and result.status not in {"ok", "error", "token_cap"}:
        result.status = "step_cap"
    result.elapsed_ms = round((time.perf_counter() - started) * 1000)
    return result


def main() -> None:
    """Run one question or an interactive command-line session."""
    import argparse

    parser = argparse.ArgumentParser(description="Ask the local SQL calculation agent a question.")
    parser.add_argument("question", nargs="*", help="Question to answer; omit for interactive mode.")
    args = parser.parse_args()

    def ask(question: str) -> None:
        result = run_agent(question)
        try:
            log_interaction(result)
        except OSError as exc:
            print(f"Warning: could not save conversation log: {exc}")
        print(f"\nAnswer: {result.answer}")
        print(f"Status: {result.status} | Steps: {result.steps} | Time: {result.elapsed_ms} ms")

    if args.question:
        ask(" ".join(args.question))
        return

    print("Local SQL agent. Type a question, or type 'exit' to quit.")
    while True:
        try:
            question = input("\nQuestion> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return
        if question.lower() in {"exit", "quit"}:
            print("Goodbye.")
            return
        if question:
            ask(question)


if __name__ == "__main__":
    main()
