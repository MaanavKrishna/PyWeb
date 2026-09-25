# PyWeb — Python from browser to database

> One language. Every layer.

Write one coherent Python app. PyWeb infers browser/server placement,
lowers plain variables to fine-grained reactive signals (no VDOM), and
generates typed RPC — no manual APIs, no state library, no bundler config.

```python
from pyweb import App

app = App()

@app.page("/")
def Home():
    count = 0

    def increment():
        count += 1

    <main>
        <h1>Counter</h1>
        <button onclick={increment}>
            Count: {count}
        </button>
    </main>
```

```bash
pip install -e .
python -m pyweb.cli inspect examples/counter/app.pyweb
python -m pyweb.cli build examples/counter/app.pyweb --out dist
python -m pyweb.cli dev examples/counter/app.pyweb   # http://localhost:8000
python -m pytest tests/ -q
```

See `ARCHITECTURE.md` for the full design (reactivity, PIR typed IR,
placement, RPC, security, roadmap).
