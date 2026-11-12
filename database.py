import os

import mysql.connector


def db():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "fuzzywirebouquet"),
    )


def query_all(sql, params=()):
    conn = db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()


def query_one(sql, params=()):
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
