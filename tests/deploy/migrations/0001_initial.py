"""initial

- create table orders"""

revision = '0001_initial'
schema = '2bf76643bc2fbf45'
contract = False


def up(op):
    op.create_table('orders', [
        op.column('id', 'bigint', nullable=False, primary_key=True, autoincrement=True),
        op.column('item', 'str', nullable=False, max_length=60),
        op.column('status', 'str', nullable=False, default='new'),
        op.column('shipped_by', 'str', nullable=False, default=''),
    ])


def down(op):
    op.drop_table('orders')
