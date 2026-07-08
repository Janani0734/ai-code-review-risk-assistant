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
from java_analyzer import analyze_java_source

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
            language TEXT,
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


def detect_language(filename: str) -> str:
    """Detect source language from file extension. Defaults to Python
    for unrecognized/missing extensions, preserving prior behavior."""
    name = (filename or "").lower()
    if name.endswith(".java"):
        return "java"
    return "python"


@app.route("/api/analyze", methods=["POST"])
def analyze():
    payload = request.get_json(silent=True) or {}
    code = payload.get("code", "")
    filename = payload.get("filename", "pasted_snippet.py")
    language_override = payload.get("language")  # optional explicit override

    if not code or not code.strip():
        return jsonify({"error": "No code provided."}), 400

    language = language_override if language_override in ("python", "java") else detect_language(filename)

    if language == "java":
        report = analyze_java_source(code)
    else:
        report = analyze_source(code)

    report["filename"] = filename
    report["analyzed_at"] = datetime.now(timezone.utc).isoformat()

    # Persist to SQLite
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO reports (filename, language, created_at, risk_level, risk_score, "
            "num_findings, report_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                filename,
                language,
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
        "SELECT id, filename, language, created_at, risk_level, risk_score, num_findings "
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


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
