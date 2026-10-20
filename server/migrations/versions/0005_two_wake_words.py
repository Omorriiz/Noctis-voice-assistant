"""wake words: only "nils" and "alexa" are offered; devices with any other word return to "nils"

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # revision растёт, чтобы включённая колонка получила новый config и сменила слово
    op.execute(sa.text("UPDATE settings SET wake_word = 'nils', revision = revision + 1 "
                       "WHERE wake_word NOT IN ('nils', 'alexa')"))
    op.execute(sa.text("DELETE FROM wake_models WHERE wake_word <> 'nils'"))


def downgrade() -> None:
    pass   # прежние слова не восстанавливаются: данные о них не хранились
