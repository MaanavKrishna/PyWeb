"""0.5 migrations: diff, expand/contract, renames, rebuilds, locks, adopt, squash, CLI."""

import os
import threading

import pytest

from pyweb import models as M
from pyweb.db import connect, migrate as mig, schema as S
from pyweb.models import Field, Model


@pytest.fixture(autouse=True)
def fresh_default(monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    yield


def models_v1():
    class Team(Model):
        name: str = Field(max=50, unique=True)

    class Player(Model):
        name: str
        team: Team
        number: int = 0
    return Team, Player


def tables_of(db):
    return set(S.introspect(db))


def test_diff_creates_everything_then_nothing(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    Team, Player = models_v1()
    d = str(tmp_path / "migrations")
    paths, plan = mig.make_migration(db, [Team, Player], d)
    assert len(paths) == 1 and os.path.basename(paths[0]) == "0001_create_teams_players.py"
    text = open(paths[0]).read()
    assert "op.create_table('teams'" in text and text.index("'teams'") < text.index("'players'")
    assert "def down(op):" in text and "op.drop_table('players')" in text
    assert mig.upgrade(db, d) == ["0001_create_teams_players"]
    assert tables_of(db) == {"teams", "players"}
    paths, plan = mig.make_migration(db, [Team, Player], d)
    assert paths == [] and not plan                          # introspection matches the models exactly
    Team.bind(db)
    Player.bind(db)
    t = Team.create(name="Reds")
    Player.create(name="Ann", team=t)
    assert Player.where(team=t).count() == 1


def test_new_optional_column_is_one_safe_step(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    d = str(tmp_path / "m")
    Team, Player = models_v1()
    mig.make_migration(db, [Team, Player], d)
    mig.upgrade(db, d)

    class Player(Model):                                     # noqa: F811 - the next version
        name: str
        team: Team
        number: int = 0
        nickname: str | None = None
    paths, plan = mig.make_migration(db, [Team, Player], d)
    assert len(paths) == 1 and not plan.contract
    assert "op.add_column('players', op.column('nickname', 'str'))" in open(paths[0]).read()
    mig.upgrade(db, d)
    assert S.introspect(db)["players"].column("nickname").nullable


def test_required_column_is_split_into_expand_and_contract(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    d = str(tmp_path / "m")
    Team, Player = models_v1()
    mig.make_migration(db, [Team, Player], d)
    mig.upgrade(db, d)
    Team.bind(db)
    Player.bind(db)
    Player.create(name="Ann", team=Team.create(name="Reds"))

    class Player(Model):                                     # noqa: F811
        name: str
        team: Team
        number: int = 0
        position: str                                        # required, no default
    paths, plan = mig.make_migration(db, [Team, Player], d)
    assert len(paths) == 2 and paths[1].endswith("_contract.py")
    assert any("fill it in" in n for n in plan.notes)
    assert "contract = True" in open(paths[1]).read()
    assert mig.upgrade(db, d) == [os.path.basename(paths[0])[:-3]]       # contract waits
    assert [m.label for m in mig.pending(db, d)] == [os.path.basename(paths[1])[:-3]]
    db.execute("UPDATE players SET position = 'keeper'")
    assert mig.upgrade(db, d, contract=True) == [os.path.basename(paths[1])[:-3]]
    col = S.introspect(db)["players"].column("position")
    assert col.nullable is False


def test_rename_copies_data_and_drops_the_old_column_later(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    d = str(tmp_path / "m")
    Team, Player = models_v1()
    mig.make_migration(db, [Team, Player], d)
    mig.upgrade(db, d)
    Team.bind(db)
    Player.bind(db)
    Player.create(name="Ann", team=Team.create(name="Reds"), number=9)

    class Player(Model):                                     # noqa: F811
        name: str
        team: Team
        shirt: int = 0
    paths, plan = mig.make_migration(db, [Team, Player], d, renames={"players.number": "shirt"})
    assert len(paths) == 2
    mig.upgrade(db, d)
    assert db.execute("SELECT number, shirt FROM players").fetchall() == [(9, 9)]   # both, while old code runs
    mig.upgrade(db, d, contract=True)
    assert db.execute("SELECT shirt FROM players").fetchall() == [(9,)]
    assert S.introspect(db)["players"].column("number") is None
    assert mig.make_migration(db, [Team, Player], d)[0] == []


def test_sqlite_rebuild_keeps_child_rows_and_indexes(tmp_path):
    """Dropping a column from a parent table rebuilds it; ON DELETE CASCADE must not fire."""
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    d = str(tmp_path / "m")

    class Team(Model):
        name: str = Field(unique=True)
        city: str | None = None

    class Player(Model):
        name: str
        team: Team
    mig.make_migration(db, [Team, Player], d)
    mig.upgrade(db, d)
    Team.bind(db)
    Player.bind(db)
    reds = Team.create(name="Reds", city="X")
    Player.create(name="Ann", team=reds)

    class Team(Model):                                       # noqa: F811
        name: str = Field(unique=True)
    paths, plan = mig.make_migration(db, [Team, Player], d, allow_destructive=True)
    assert len(paths) == 1 and "drop column teams.city" in open(paths[0]).read()
    mig.upgrade(db, d)
    assert db.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 1      # child survived
    live = S.introspect(db)["teams"]
    assert live.column("city") is None and any(i.unique and i.columns == ("name",) for i in live.indexes)
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1               # back on afterwards
    with pytest.raises(Exception):
        db.execute("INSERT INTO players (name, team_id) VALUES ('Bob', 999)")   # still enforced


def test_alter_column_type_and_nullability(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    d = str(tmp_path / "m")

    class Score(Model):
        points: int | None = None
    mig.make_migration(db, [Score], d)
    mig.upgrade(db, d)
    db.execute("INSERT INTO scores (points) VALUES (3)")

    class Score(Model):                                      # noqa: F811
        points: float = 0.0
    paths, plan = mig.make_migration(db, [Score], d, allow_destructive=True)
    assert any(isinstance(op, S.AlterColumn) for op in plan.ops)
    mig.upgrade(db, d)
    col = S.introspect(db)["scores"].column("points")
    assert col.kind == "float" and not col.nullable
    assert db.execute("SELECT points FROM scores").fetchall() == [(3.0,)]


def test_downgrade_walks_back(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    d = str(tmp_path / "m")
    Team, Player = models_v1()
    mig.make_migration(db, [Team, Player], d)
    mig.upgrade(db, d)

    class Player(Model):                                     # noqa: F811
        name: str
        team: Team
        number: int = 0
        nickname: str | None = None
    mig.make_migration(db, [Team, Player], d)
    mig.upgrade(db, d)
    assert mig.downgrade(db, d) == ["0002_add_column_players_nickname"]
    assert S.introspect(db)["players"].column("nickname") is None
    assert mig.downgrade(db, d, steps=5) == ["0001_create_teams_players"]
    assert tables_of(db) == set()


def test_diff_refuses_when_migrations_are_pending(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    d = str(tmp_path / "m")
    Team, Player = models_v1()
    mig.make_migration(db, [Team, Player], d)
    with pytest.raises(RuntimeError, match="pending"):
        mig.make_migration(db, [Team, Player], d)


def test_unused_tables_are_noted_not_dropped(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    db.execute("CREATE TABLE legacy_stuff (id INTEGER PRIMARY KEY)")
    Team, Player = models_v1()
    paths, plan = mig.make_migration(db, [Team, Player], str(tmp_path / "m"))
    assert any("legacy_stuff" in n for n in plan.notes)
    up = open(paths[0]).read().split("def down")[0]
    assert "drop_table" not in up


def test_lock_lets_one_process_migrate(tmp_path):
    url = f"sqlite:///{tmp_path / 'shared.db'}"
    d = str(tmp_path / "m")
    os.makedirs(d)
    with open(os.path.join(d, "0001_slow.py"), "w") as fh:
        fh.write("import time\n\ndef up(op):\n    time.sleep(0.3)\n    op.execute('CREATE TABLE things (id INTEGER PRIMARY KEY)')\n\n"
                 "def down(op):\n    op.execute('DROP TABLE things')\n")
    results, errors = [], []

    def run():
        try:
            results.append(mig.upgrade(connect(url), d))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and sorted(map(len, results)) == [0, 0, 0, 1]


def test_lock_times_out_with_a_clear_message(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    with mig.lock(db):
        with pytest.raises(TimeoutError, match="still migrating"):
            with mig.lock(connect(f"sqlite:///{tmp_path / 'a.db'}"), timeout=0.3):
                pass
    with mig.lock(db, timeout=1):                            # released afterwards
        pass


def test_adopt_records_an_existing_database(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'old.db'}")
    Team, Player = models_v1()
    M.ensure_tables(db, [Team, Player])                      # a database made without migrations
    d = str(tmp_path / "m")
    path = mig.adopt(db, d)
    assert path.endswith("0001_initial.py") and mig.status(db, d) == [("0001_initial", True)]
    assert mig.make_migration(db, [Team, Player], d)[0] == []
    fresh = connect(f"sqlite:///{tmp_path / 'new.db'}")
    mig.upgrade(fresh, d)                                    # and it builds new databases too
    assert tables_of(fresh) == {"teams", "players"}
    with pytest.raises(RuntimeError, match="already has migrations"):
        mig.adopt(db, d)


def test_squash_replaces_history(tmp_path):
    old = connect(f"sqlite:///{tmp_path / 'old.db'}")
    d = str(tmp_path / "m")
    Team, Player = models_v1()
    mig.make_migration(old, [Team], d)
    mig.upgrade(old, d)
    mig.make_migration(old, [Team, Player], d)
    mig.upgrade(old, d)
    path = mig.squash(old, d)
    assert "replaces = ['0001_create_teams', '0002_create_players']" in open(path).read()
    assert mig.upgrade(old, d) == []                         # already there: recorded, not run
    new = connect(f"sqlite:///{tmp_path / 'new.db'}")
    assert mig.upgrade(new, d) == ["0003_squashed"]          # only the squashed one runs
    assert tables_of(new) == {"teams", "players"}


def test_sql_and_python_migrations_mix_in_order(tmp_path):
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    d = str(tmp_path / "m")
    os.makedirs(d)
    with open(os.path.join(d, "001_a.up.sql"), "w") as fh:
        fh.write("CREATE TABLE a (id INTEGER PRIMARY KEY);")
    with open(os.path.join(d, "001_a.down.sql"), "w") as fh:
        fh.write("DROP TABLE a;")
    mig.new_python_migration(d, "add b")
    path = os.path.join(d, "0002_add_b.py")
    text = open(path).read().replace("def up(op):\n    pass", "def up(op):\n    op.execute('CREATE TABLE b (id INTEGER)')")
    text = text.replace("def down(op):\n    pass", "def down(op):\n    op.drop_table('b')")
    open(path, "w").write(text)
    assert mig.upgrade(db, d) == ["001_a", "0002_add_b"]
    assert mig.downgrade(db, d, steps=2) == ["0002_add_b", "001_a"]


def test_types_survive_introspection_on_sqlite(tmp_path):
    import datetime as dt
    import decimal

    class Mixed(Model):
        a: int = 0
        b: str = Field("", max=10)
        c: float = 0.0
        d: bool = False
        e: decimal.Decimal | None = None
        f: dt.datetime | None = None
        g: dict | None = None
        h: bytes | None = None
    db = connect(f"sqlite:///{tmp_path / 'a.db'}")
    M.ensure_tables(db, [Mixed])
    plan = S.diff(S.introspect(db), S.from_models([Mixed]), db.dialect)
    assert not plan, [op.describe() for op in plan.ops]


# ------------------------------------------------------------------- the CLI

APP = """from pyweb import App, Model, Field
app = App(database="sqlite:///{db}")

class Task(Model):
    title: str = Field(max=80)
    done: bool = False
{extra}
@app.page("/")
def Home():
    <p>{{Task.count()}}</p>
"""


def test_cli_diff_upgrade_status_seed(tmp_path, capsys, monkeypatch):
    from pyweb.cli import main
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "app.db"
    (tmp_path / "app.pyweb").write_text(APP.format(db=db_path, extra=""))
    main(["db", "diff", "--app", "app.pyweb", "--name", "create tasks"])
    out = capsys.readouterr().out
    assert "wrote" in out and (tmp_path / "migrations" / "0001_create_tasks.py").exists()
    main(["db", "status", "--app", "app.pyweb"])
    assert "[ ] 0001_create_tasks" in capsys.readouterr().out
    main(["db", "upgrade", "--app", "app.pyweb"])
    assert "applied 1 migration(s): 0001_create_tasks" in capsys.readouterr().out
    main(["db", "diff", "--app", "app.pyweb"])
    assert "no changes" in capsys.readouterr().out
    (tmp_path / "seeds.py").write_text(
        "from pyweb.db.seeds import seed\nfrom pyweb.models import all_models\n\n@seed\ndef tasks():\n"
        "    Task = [m for m in all_models() if m.__name__ == 'Task'][-1]\n"
        "    Task.get_or_create(title='First task')\n")
    main(["db", "seed", "--app", "app.pyweb"])
    main(["db", "seed", "--app", "app.pyweb"])
    assert "seeded: tasks" in capsys.readouterr().out
    import sqlite3
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    (tmp_path / "app.pyweb").write_text(APP.format(db=db_path, extra="    notes: str | None = None\n"))
    main(["db", "diff", "--app", "app.pyweb"])
    main(["db", "status", "--app", "app.pyweb"])
    assert "[ ] 0002_add_column_tasks_notes" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="pending"):
        main(["db", "diff", "--app", "app.pyweb"])


def test_dev_server_applies_migrations_and_prototype_mode_creates_tables(tmp_path, monkeypatch):
    from pyweb.hosting import Site
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    proto = tmp_path / "proto"
    proto.mkdir()
    (proto / "app.pyweb").write_text(APP.format(db=proto / "p.db", extra=""))
    site = Site(str(proto / "app.pyweb"), debug=True)
    status, _, body = site.respond("GET", "/", {})
    assert status == 200 and b"0" in body                    # no migrations folder: tables made on first use
    real = tmp_path / "real"
    (real / "migrations").mkdir(parents=True)
    (real / "app.pyweb").write_text(APP.format(db=real / "r.db", extra=""))
    from pyweb.cli import main
    main(["db", "diff", "--app", str(real / "app.pyweb")])
    site = Site(str(real / "app.pyweb"), debug=True)
    assert site.app.migrated == ["0001_create_tasks"]        # pyweb dev applied it


def test_serve_migrate_flag(tmp_path, monkeypatch):
    from pyweb import serve as _serve
    from pyweb.cli import main
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.pyweb").write_text(APP.format(db=tmp_path / "s.db", extra=""))
    main(["db", "diff", "--app", "app.pyweb"])
    main(["build", "app.pyweb", "--out", str(tmp_path / "dist")])
    assert (tmp_path / "dist" / "migrations" / "0001_create_tasks.py").exists()
    monkeypatch.setenv("PYWEB_ENV", "production")
    monkeypatch.setenv("PYWEB_AUTH_SECRET", "x" * 40)
    httpd = _serve.serve(str(tmp_path / "dist"), host="127.0.0.1", port=0, migrate=True)
    httpd.server_close()
    import sqlite3
    rows = sqlite3.connect(tmp_path / "s.db").execute("SELECT version FROM pyweb_migrations").fetchall()
    assert rows == [("0001_create_tasks",)]
