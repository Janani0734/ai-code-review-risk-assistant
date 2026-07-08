"""
Integration tests for language detection/routing in the Flask API.
Run with: pytest tests/ -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from app import app, init_db  # noqa: E402


@pytest.fixture
def client():
    init_db()
    with app.test_client() as c:
        yield c


def test_python_extension_routes_to_python_analyzer(client):
    code = "def add(a, b):\n    return a + b\n"
    r = client.post("/api/analyze", json={"code": code, "filename": "utils.py"})
    assert r.status_code == 200
    assert r.get_json()["language"] == "python"


def test_java_extension_routes_to_java_analyzer(client):
    code = "public class Add { public int add(int a, int b) { return a + b; } }"
    r = client.post("/api/analyze", json={"code": code, "filename": "Add.java"})
    assert r.status_code == 200
    assert r.get_json()["language"] == "java"


def test_missing_filename_defaults_to_python(client):
    code = "def add(a, b):\n    return a + b\n"
    r = client.post("/api/analyze", json={"code": code})
    assert r.status_code == 200
    assert r.get_json()["language"] == "python"


def test_explicit_language_override(client):
    code = "public class Add { public int add(int a, int b) { return a + b; } }"
    # filename says .txt, but explicit override should force java
    r = client.post("/api/analyze", json={"code": code, "filename": "snippet.txt", "language": "java"})
    assert r.status_code == 200
    assert r.get_json()["language"] == "java"


def test_history_includes_language_column(client):
    client.post("/api/analyze", json={"code": "def f(): pass", "filename": "a.py"})
    client.post("/api/analyze", json={
        "code": "public class B { public void f() {} }",
        "filename": "B.java",
    })
    r = client.get("/api/history")
    rows = r.get_json()
    languages = {row["language"] for row in rows}
    assert "python" in languages
    assert "java" in languages
