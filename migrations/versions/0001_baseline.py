"""Frozen baseline for the five inventory tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "run",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text()),
        sa.Column("matched_name_count", sa.Integer()),
        sa.Column("matched_fqdn_count", sa.Integer()),
        sa.Column("matched_ip_count", sa.Integer()),
        sa.Column("no_match_count", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint("status IN ('RUNNING', 'SUCCESS', 'FAIL')"),
        sa.CheckConstraint("matched_name_count >= 0"),
        sa.CheckConstraint("matched_fqdn_count >= 0"),
        sa.CheckConstraint("matched_ip_count >= 0"),
        sa.CheckConstraint("no_match_count >= 0"),
    )
    op.create_table(
        "asset",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("vm_id", sa.Text(), nullable=False),
        sa.Column("vm_name", sa.Text()),
        sa.Column("fqdn", sa.Text()),
        sa.Column("ip_reported", sa.Text()),
        sa.Column("node", sa.Text()),
        sa.Column("type", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("tags", sa.Text()),
        sa.Column("role", sa.Text()),
        sa.Column("os", sa.Text()),
        sa.Column("annotation", sa.Text()),
        sa.Column("cpu", sa.REAL()),
        sa.Column("ram", sa.REAL()),
        sa.Column("disk", sa.REAL()),
        sa.Column("uptime", sa.REAL()),
        sa.CheckConstraint("type IN ('qemu', 'lxc')"),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("id", "run_id"),
    )
    op.create_table(
        "ipam_record",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("ip", sa.Text()),
        sa.Column("dns_name", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("tenant", sa.Text()),
        sa.Column("site", sa.Text()),
        sa.Column("tags", sa.Text()),
        sa.Column("duplicate_dns", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("duplicate_ip", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("meta_zone", sa.Text()),
        sa.Column("custom_fields", sa.Text()),
        sa.CheckConstraint("duplicate_dns IN (0, 1)"),
        sa.CheckConstraint("duplicate_ip IN (0, 1)"),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("id", "run_id"),
    )
    op.create_table(
        "consolidated_asset",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("ipam_record_id", sa.Integer()),
        sa.Column("ip_final", sa.Text()),
        sa.Column("dns_final", sa.Text()),
        sa.Column("status_final", sa.Text()),
        sa.Column("match_status", sa.Text(), nullable=False),
        sa.Column("role", sa.Text()),
        sa.CheckConstraint("match_status IN ('MATCHED_NAME', 'MATCHED_FQDN', 'MATCHED_IP', 'NO_MATCH')"),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["asset_id", "run_id"], ["asset.id", "asset.run_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["ipam_record_id", "run_id"], ["ipam_record.id", "ipam_record.run_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("asset_id", "run_id"),
    )
    op.create_table(
        "anomaly",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer()),
        sa.Column("ipam_record_id", sa.Integer()),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "code IN ('NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', "
            "'STATUS_MISMATCH', 'DUPLICATE_DNS', 'DUPLICATE_IP')"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["asset_id", "run_id"], ["asset.id", "asset.run_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ipam_record_id", "run_id"], ["ipam_record.id", "ipam_record.run_id"],
            ondelete="RESTRICT",
        ),
    )

    for name, table, columns in (
        ("idx_asset_run", "asset", ("run_id",)),
        ("idx_asset_run_vm", "asset", ("run_id", "vm_id")),
        ("idx_asset_run_name", "asset", ("run_id", "vm_name")),
        ("idx_asset_run_fqdn", "asset", ("run_id", "fqdn")),
        ("idx_asset_run_ip", "asset", ("run_id", "ip_reported")),
        ("idx_asset_run_node", "asset", ("run_id", "node")),
        ("idx_ipam_run_ip", "ipam_record", ("run_id", "ip")),
        ("idx_ipam_run_dns", "ipam_record", ("run_id", "dns_name")),
        ("idx_ipam_run_zone", "ipam_record", ("run_id", "meta_zone")),
        ("idx_consolidated_run", "consolidated_asset", ("run_id",)),
        ("idx_consolidated_ipam_run", "consolidated_asset", ("ipam_record_id", "run_id")),
        ("idx_consolidated_run_match", "consolidated_asset", ("run_id", "match_status")),
        ("idx_anomaly_run", "anomaly", ("run_id",)),
        ("idx_anomaly_asset_run", "anomaly", ("asset_id", "run_id")),
        ("idx_anomaly_ipam_run", "anomaly", ("ipam_record_id", "run_id")),
    ):
        op.create_index(name, table, columns)


def downgrade():
    op.drop_table("anomaly")
    op.drop_table("consolidated_asset")
    op.drop_table("ipam_record")
    op.drop_table("asset")
    op.drop_table("run")
