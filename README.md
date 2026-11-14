# Local SQL Calculation Agent

A small Python agent that answers calculation questions using a local SQLite
database and a local Ollama model. No hosted API is required.

## Quick start (PowerShell)

Run these four commands from PowerShell:

```powershell
cd "F:\books\7th sem\Agentic SE L\SQL agent"
```

```powershell
Start-Process ollama -ArgumentList "serve"; .\.venv\Scripts\python.exe build_database.py
```

```powershell
ollama pull qwen2.5:3b
```

```powershell
.\.venv\Scripts\python.exe agent.py
```

Then ask questions:

```text
Question> How many customers are there?
Question> What is the total revenue for customer 12?
Question> exit
```

Prerequisites: Python, Ollama, and the existing `.venv` environment must be
installed. If Ollama is already running, omit `Start-Process ollama ...`.

## Other useful commands

Run one question:

```powershell
.\.venv\Scripts\python.exe agent.py "How many customers are there?"
```

Run the test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

View saved questions and answers:

```powershell
Get-Content .\artifacts\conversation_log.jsonl
```

Each log line contains the UTC timestamp, question, answer, status, and response
time in milliseconds.

## Database

`build_database.py` creates the deterministic SQLite database at
`data/sales.db` with the `customers`, `products`, `orders`, and `order_items`
tables.
