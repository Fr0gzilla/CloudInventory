"""Add observation origins, accounts, role references and persistent audit."""

import sqlalchemy as sa
from alembic import op

revision = "0003_model_v23"
down_revision = "0002_model_v22"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("asset", sa.Column("virtual_source", sa.Text()))
    op.add_column("ipam_record", sa.Column("ipam_source", sa.Text()))

    op.create_table(
        "auth_origin",
        sa.Column("origin_code", sa.Text(), primary_key=True, nullable=True),
        sa.CheckConstraint("origin_code IN ('local', 'LDAP', 'AD')"),
    )
    op.create_table(
        "role",
        sa.Column("role_code", sa.Text(), primary_key=True, nullable=True),
        sa.CheckConstraint("role_code IN ('admin', 'opérateur', 'lecteur')"),
    )
    op.create_table(
        "permission",
        sa.Column("permission_code", sa.Text(), primary_key=True, nullable=True),
        sa.CheckConstraint("length(trim(permission_code)) > 0"),
    )
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("origin_code", sa.Text(), sa.ForeignKey("auth_origin.origin_code", ondelete="RESTRICT"), nullable=False),
        sa.Column("role_code", sa.Text(), sa.ForeignKey("role.role_code", ondelete="RESTRICT"), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("local_password_hash", sa.Text()),
        sa.UniqueConstraint("origin_code", "username"),
        sa.CheckConstraint("length(trim(username)) > 0"),
        sa.CheckConstraint(
            "(origin_code = 'local' AND local_password_hash IS NOT NULL AND length(trim(local_password_hash)) > 0) "
            "OR (origin_code <> 'local' AND local_password_hash IS NULL)"
        ),
    )
    op.create_table(
        "role_permission",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("role_code", sa.Text(), sa.ForeignKey("role.role_code", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_code", sa.Text(), sa.ForeignKey("permission.permission_code", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("role_code", "permission_code"),
    )
    op.create_table(
        "audit",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="SET NULL")),
        sa.Column("actor_identity", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.CheckConstraint("length(trim(actor_identity)) > 0"),
        sa.CheckConstraint("length(trim(action)) > 0"),
        sa.CheckConstraint("length(trim(target)) > 0"),
    )
    op.create_index("idx_account_role", "account", ["role_code"])
    op.create_index("idx_role_permission_permission", "role_permission", ["permission_code"])
    op.create_index("idx_audit_account", "audit", ["account_id"])
    op.create_index("idx_audit_date", "audit", ["created_at"])


def downgrade():
    op.drop_index("idx_audit_date", "audit")
    op.drop_index("idx_audit_account", "audit")
    op.drop_index("idx_role_permission_permission", "role_permission")
    op.drop_index("idx_account_role", "account")
    op.drop_table("audit")
    op.drop_table("role_permission")
    op.drop_table("account")
    op.drop_table("permission")
    op.drop_table("role")
    op.drop_table("auth_origin")
    with op.batch_alter_table("ipam_record") as batch:
        batch.drop_column("ipam_source")
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("virtual_source")
