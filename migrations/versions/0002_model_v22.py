"""Extend the inventory schema to the published v2.2 model."""

import json
import os

import sqlalchemy as sa
from alembic import op

revision = "0002_model_v22"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

CODES = "'NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', 'STATUS_MISMATCH', 'DUPLICATE_DNS', 'DUPLICATE_IP', 'RENAMED'"
SEVERITIES = ("info", "mineure", "majeure", "critique")


def upgrade():
    connection = op.get_bind()
    existing = connection.execute(sa.text("SELECT DISTINCT code FROM anomaly")).scalars().all()
    classification = json.loads(os.environ.get("ANOMALY_SEVERITIES", "{}"))
    if not isinstance(classification, dict) or any(classification.get(code) not in SEVERITIES for code in existing):
        raise ValueError("Classify all historical anomaly codes in ANOMALY_SEVERITIES before upgrading")

    op.create_table(
        "run_schedule",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("frequency", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_at", sa.Text()),
        sa.CheckConstraint("frequency IN ('horaire', 'quotidien')"),
        sa.CheckConstraint("enabled IN (0, 1)"),
    )
    op.add_column("run", sa.Column("current_step", sa.Integer()))
    op.add_column("run", sa.Column("completed_steps", sa.Integer()))
    op.add_column("run", sa.Column("run_trigger", sa.Text()))
    op.add_column("run", sa.Column("schedule_id", sa.Integer()))
    with op.batch_alter_table("run", recreate="always") as batch:
        batch.create_check_constraint("ck_run_status", "status IN ('RUNNING', 'SUCCESS', 'FAIL')")
        for name in ("matched_name_count", "matched_fqdn_count", "matched_ip_count", "no_match_count"):
            batch.create_check_constraint(f"ck_run_{name}", f"{name} >= 0")
        batch.create_check_constraint("ck_run_current_step", "current_step BETWEEN 1 AND 7")
        batch.create_check_constraint("ck_run_completed_steps", "completed_steps BETWEEN 0 AND 7")
        batch.create_check_constraint("ck_run_trigger", "run_trigger IN ('manuel', 'planifié')")
        batch.create_foreign_key("fk_run_schedule", "run_schedule", ["schedule_id"], ["id"], ondelete="SET NULL")
    op.create_index("idx_run_schedule", "run", ["schedule_id"])

    op.create_table(
        "anomaly_exception",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.CheckConstraint(f"code IN ({CODES})"),
        sa.CheckConstraint("length(trim(target)) > 0"),
        sa.CheckConstraint("length(trim(justification)) > 0"),
        sa.CheckConstraint("length(trim(author)) > 0"),
    )

    op.create_table(
        "_anomaly_v22",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer()),
        sa.Column("ipam_record_id", sa.Integer()),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("tracking_key", sa.Text()),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'ouverte'")),
        sa.Column("state_changed_at", sa.Text()),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.CheckConstraint(f"code IN ({CODES})"),
        sa.CheckConstraint("state IN ('ouverte', 'acquittée', 'résolue')"),
        sa.CheckConstraint("severity IN ('info', 'mineure', 'majeure', 'critique')"),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id", "run_id"], ["asset.id", "asset.run_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ipam_record_id", "run_id"], ["ipam_record.id", "ipam_record.run_id"], ondelete="RESTRICT"),
    )
    for code in existing:
        connection.execute(sa.text(
            "INSERT INTO _anomaly_v22 (id, run_id, asset_id, ipam_record_id, code, description, created_at, severity) "
            "SELECT id, run_id, asset_id, ipam_record_id, code, description, created_at, :severity "
            "FROM anomaly WHERE code = :code"
        ), {"severity": classification[code], "code": code})
    op.drop_table("anomaly")
    op.rename_table("_anomaly_v22", "anomaly")
    op.create_index("idx_anomaly_run", "anomaly", ["run_id"])
    op.create_index("idx_anomaly_asset_run", "anomaly", ["asset_id", "run_id"])
    op.create_index("idx_anomaly_ipam_run", "anomaly", ["ipam_record_id", "run_id"])
    op.create_index("idx_anomaly_tracking", "anomaly", ["tracking_key", "created_at"])
    op.create_index("idx_anomaly_severity", "anomaly", ["severity"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM anomaly WHERE code = 'RENAMED' LIMIT 1")).first():
        raise ValueError("Cannot downgrade while RENAMED anomalies exist")
    op.drop_index("idx_anomaly_severity", "anomaly")
    op.drop_index("idx_anomaly_tracking", "anomaly")
    with op.batch_alter_table("anomaly", recreate="always") as batch:
        batch.drop_column("severity")
        batch.drop_column("state_changed_at")
        batch.drop_column("state")
        batch.drop_column("tracking_key")
        batch.create_check_constraint("ck_anomaly_code_old", "code IN ('NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', 'STATUS_MISMATCH', 'DUPLICATE_DNS', 'DUPLICATE_IP')")
    op.drop_table("anomaly_exception")
    op.drop_index("idx_run_schedule", "run")
    with op.batch_alter_table("run", recreate="always") as batch:
        batch.drop_constraint("ck_run_trigger", type_="check")
        batch.drop_constraint("ck_run_current_step", type_="check")
        batch.drop_constraint("ck_run_completed_steps", type_="check")
        batch.drop_constraint("fk_run_schedule", type_="foreignkey")
        batch.drop_column("schedule_id")
        batch.drop_column("run_trigger")
        batch.drop_column("completed_steps")
        batch.drop_column("current_step")
    op.drop_table("run_schedule")
