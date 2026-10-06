"""Row patches for live queries: whatever the change, old rows + patch == new rows."""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from pyweb import livedata as L

rows_strategy = st.lists(
    st.fixed_dictionaries({"id": st.integers(0, 30), "title": st.sampled_from(["a", "b", "c", "d"]),
                           "n": st.integers(0, 3)}),
    max_size=25,
).map(lambda rows: list({r["id"]: r for r in rows}.values()))          # unique ids


@settings(max_examples=600, deadline=None)
@given(rows_strategy, rows_strategy, st.randoms())
def test_patch_turns_old_rows_into_new_rows(old, new, rnd):
    rnd.shuffle(new)
    ops = L.diff(old, new, "id")
    assert ops is not None
    assert L.apply(old, ops, "id") == new


@settings(max_examples=300, deadline=None)
@given(rows_strategy)
def test_unchanged_rows_keep_their_identity(rows):
    if not rows:
        return
    new = [dict(r) for r in rows]
    new[0] = dict(new[0], title="changed!")
    out = L.apply(rows, L.diff(rows, new, "id"), "id")
    assert out == new
    assert all(a is b for a, b in zip(out[1:], rows[1:]))        # the browser keeps those rows' DOM


def test_one_changed_row_sends_one_row():
    old = [{"id": i, "title": f"task {i}", "done": False} for i in range(500)]
    new = [dict(r) for r in old]
    new[250]["done"] = True
    msg = L.change_message(old, new, "id", "v1", "v2")
    assert msg["prev"] == "v1" and msg["ops"] == [["u", new[250]]]
    assert len(json.dumps(msg)) < 200                            # well under 1 KB
    assert len(json.dumps({"rows": new})) > 15_000


def test_insert_delete_and_move():
    old = [{"id": 1}, {"id": 2}, {"id": 3}]
    assert L.diff(old, [{"id": 1}, {"id": 4}, {"id": 2}, {"id": 3}], "id") == [["i", 1, {"id": 4}]]
    assert L.diff(old, [{"id": 1}, {"id": 3}], "id") == [["d", 2]]
    assert L.diff(old, [{"id": 3}, {"id": 1}, {"id": 2}], "id") == [["o", [3, 1, 2]]]


def test_unkeyable_rows_send_everything():
    assert L.diff([{"x": 1}], [{"x": 2}], "id") is None
    assert L.diff([{"id": 1}, {"id": 1}], [{"id": 1}], "id") is None
    assert L.diff([{"id": 1}], [{"id": 2}], None) is None
    msg = L.change_message([{"x": 1}], [{"x": 2}], None, "v1", "v2")
    assert msg == {"version": "v2", "rows": [{"x": 2}]}


def test_a_patch_bigger_than_the_rows_sends_the_rows():
    old = [{"id": i} for i in range(10)]
    new = [{"id": i + 100} for i in range(10)]                 # everything replaced
    assert "rows" in L.change_message(old, new, "id", "v1", "v2")


def test_first_message_has_no_prev():
    assert L.change_message(None, [{"id": 1}], "id", None, "v1") == {"version": "v1", "rows": [{"id": 1}]}


def test_model_queries_with_live_make_a_live_page():
    from pyweb.compiler import compile_source
    out = compile_source('''
from pyweb import App
from pyweb.models import Model

app = App(database="sqlite:///:memory:")

class Order(Model):
    item: str

@app.page("/")
def Home():
    orders = Order.query().order("-id").limit(20).live()
    <ul>
        for o in orders:
            <li>{o["item"]}</li>
    </ul>
''', filename="app.pyweb")
    js = out["pages"]["Home"]["js"]
    assert '$live(orders, $s["$live:orders"])' in js and 'from "./live.js"' in js
