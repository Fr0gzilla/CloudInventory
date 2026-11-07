"""Add per-account session revocation version."""

import sqlalchemy as sa
from alembic import op

revision = "0005_account_session_version"
down_revision = "0004_model_v30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("account", sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("account", "session_version")
