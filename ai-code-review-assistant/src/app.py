"""
app.py
Flask REST API for the AI Code Review Risk Assistant.

Endpoints:
    GET  /                -> serves the simple HTML frontend
    POST /api/analyze     -> accepts { "code": "<source>", "filename": "optional" }
                              returns a JSON risk report
    GET  /api/history      -> returns past analysis records from SQLite
    GET  /api/health       -> health check
"""

import os
import sqlite3
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory

from analyzer import analyze_source

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "data", "reports.db")
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            created_at TEXT,
            risk_level TEXT,
            risk_score INTEGER,
            num_findings INTEGER,
            report_json TEXT
        )
    """)
    conn.commit()
    conn.close()


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    payload = request.get_json(silent=True) or {}
    code = payload.get("code", "")
    filename = payload.get("filename", "pasted_snippet.py")

    if not code or not code.strip():
        return jsonify({"error": "No code provided."}), 400

    report = analyze_source(code)
    report["filename"] = filename
    report["analyzed_at"] = datetime.now(timezone.utc).isoformat()

    # Persist to SQLite
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO reports (filename, created_at, risk_level, risk_score, "
            "num_findings, report_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                filename,
                report["analyzed_at"],
                report["risk_level"],
                report["risk_score"],
                report["num_findings"],
                json.dumps(report),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        # Don't fail the analysis if persistence has an issue; just log it.
        report["persistence_warning"] = str(e)

    return jsonify(report)


@app.route("/api/history", methods=["GET"])
def history():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, filename, created_at, risk_level, risk_score, num_findings "
        "FROM reports ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/history/<int:report_id>", methods=["GET"])
def history_detail(report_id):
    conn = get_db()
    row = conn.execute(
        "SELECT report_json FROM reports WHERE id = ?", (report_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "Report not found."}), 404
    return jsonify(json.loads(row["report_json"]))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
