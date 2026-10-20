"""stage 5: timers finished_at, alarms last_fired_key, media album/genre/external_id

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('timers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('alarms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_fired_key', sa.String(length=16), nullable=False, server_default=''))
    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.add_column(sa.Column('album', sa.String(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('genre', sa.String(length=80), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('external_id', sa.String(length=64), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.drop_column('external_id')
        batch_op.drop_column('genre')
        batch_op.drop_column('album')
    with op.batch_alter_table('alarms', schema=None) as batch_op:
        batch_op.drop_column('last_fired_key')
    with op.batch_alter_table('timers', schema=None) as batch_op:
        batch_op.drop_column('finished_at')
