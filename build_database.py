
from __future__ import annotations

import random
import sqlite3
from pathlib import Path

SEED = 20261113
DATABASE_PATH = Path(__file__).with_name("data") / "sales.db"


def build_database(path: Path = DATABASE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(SEED)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                region TEXT NOT NULL,
                signup_date TEXT NOT NULL
            );
            CREATE TABLE products (
                product_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                unit_price REAL NOT NULL
            );
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
                order_date TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(order_id),
                product_id INTEGER NOT NULL REFERENCES products(product_id),
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL
            );
            """
        )
        regions = ["North", "South", "East", "West"]
        connection.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?)",
            [(i, f"Customer {i}", rng.choice(regions), f"2024-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}") for i in range(1, 31)],
        )
        product_rows = []
        for i in range(1, 13):
            category = ["Books", "Electronics", "Home"][i % 3]
            product_rows.append((i, f"Product {i}", category, round(8 + i * 3.75, 2)))
        connection.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", product_rows)
        order_rows = []
        for i in range(1, 121):
            customer_id = ((i * 7) % 30) + 1
            status = ["completed", "completed", "completed", "cancelled"][i % 4]
            order_rows.append((i, customer_id, f"2025-{((i - 1) % 12) + 1:02d}-{((i * 3) % 27) + 1:02d}", status))
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", order_rows)
        item_rows = []
        item_id = 1
        for order_id in range(1, 121):
            for offset in range(1, 3 + (order_id % 2)):
                product_id = ((order_id * 5 + offset * 3) % 12) + 1
                price = product_rows[product_id - 1][3]
                item_rows.append((item_id, order_id, product_id, (order_id + offset) % 5 + 1, price))
                item_id += 1
        connection.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", item_rows)
        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    build_database()
    print(f"Built {DATABASE_PATH} with seed {SEED}.")
