# Sample data

This project doesn't need a large dataset — it analyzes whatever code snippet
is pasted or uploaded at request time. This file just collects a few sample
inputs you can use to try the tool immediately.

## Sample 1: mixed issues (unused import, bare except, hardcoded secret, missing None check)

```python
import os

def get_user(user=None):
    return user.name

def risky():
    try:
        x = 1 / 0
    except:
        pass

API_KEY = "sk_live_51H8xJ2eZvKYlo2C0aBcD"
```

## Sample 2: hallucinated stdlib usage

```python
import stringutils

result = math.squareroot(16)
data = json.loads('{"a":1}', True, False)
```

## Sample 3: high cyclomatic complexity

```python
def compute(a, b, c, d, e):
    total = 0
    for i in range(a):
        if i > b:
            if i < c:
                while i < d:
                    if i == e:
                        if i % 2 == 0:
                            if i > 1:
                                total += 1
                    i += 1
    return total
```

## Sample 4: clean code (should score Low risk)

```python
def add(a, b):
    return a + b
```

## Sample 5: Java equivalent of Sample 1 (unused import, swallowed exception, hardcoded secret)

```java
import java.util.List;

public class UserService {
    private String apiKey = "sk_live_51H8xJ2eZvKYlo2C0aBcD";

    public String getUser(String user) {
        return user.name;
    }

    public void risky() {
        try {
            int x = 1 / 0;
        } catch (Exception e) {
        }
    }
}
```

Paste any of these into the web UI's textarea (use the Python/Java sample
toggle buttons to pre-load Sample 1 or Sample 5 directly), or POST them as
the `code` field to `/api/analyze` with a matching `filename` (e.g.
`"UserService.java"`), to see the risk-scored report.
