"""DB toolkit: query builder, migrations, transactions, pagination."""

from __future__ import annotations

_OPS = {
    "eq": "=", "gt": ">", "lt": "<", "gte": ">=", "lte": "<=",
    "ne": "!=", "like": "LIKE",
}


class Query:
    def __init__(self, table):
        self.table = table
        self._wheres: list[tuple] = []
        self._order: list[tuple] = []
        self._limit = None
        self._offset = None

    def where(self, **kwargs):
        for key, value in kwargs.items():
            if "__" in key:
                field, op = key.rsplit("__", 1)
                self._wheres.append((field, _OPS.get(op, "="), value))
            else:
                self._wheres.append((key, "=", value))
        return self

    def order_by(self, field, desc=False):
        self._order.append((field, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def paginate(self, page=1, per_page=20):
        return self.limit(per_page).offset((page - 1) * per_page)

    def sql(self):
        q = f'SELECT * FROM "{self.table}"'
        params: list = []
        if self._wheres:
            q += " WHERE " + " AND ".join(f'"{f}" {op} ?' for f, op, _ in self._wheres)
            params = [v for _, _, v in self._wheres]
        if self._order:
            q += " ORDER BY " + ", ".join(f'"{f}" {"DESC" if d else "ASC"}' for f, d in self._order)
        if self._limit is not None:
            q += f" LIMIT {int(self._limit)}"
        if self._offset is not None:
            q += f" OFFSET {int(self._offset)}"
        return q, params


class Migration:
    def __init__(self, name, statements):
        self.name = name
        self.statements = list(statements)

    def apply(self, conn):
        for stmt in self.statements:
            conn.execute(stmt)
        conn.commit()


class Schema:
    def __init__(self):
        self.migrations: list[Migration] = []
        self.applied: list[str] = []

    def add(self, migration):
        self.migrations.append(migration)

    def migrate(self, conn):
        for m in self.migrations:
            if m.name not in self.applied:
                m.apply(conn)
                self.applied.append(m.name)
        return list(self.applied)


class Transaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False
