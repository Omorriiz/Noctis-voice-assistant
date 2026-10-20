"""stage 7.5: users.last_login_at, audit_log (журнал действий администратора)

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('admin_email', sa.String(length=254), nullable=False, server_default=''),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('target', sa.String(length=254), nullable=False, server_default=''),
        sa.Column('ip', sa.String(length=64), nullable=False, server_default=''),
    )
    op.create_index('ix_audit_log_admin_id', 'audit_log', ['admin_id'])


def downgrade() -> None:
    op.drop_index('ix_audit_log_admin_id', table_name='audit_log')
    op.drop_table('audit_log')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('last_login_at')
