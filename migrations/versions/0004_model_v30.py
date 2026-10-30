"""Extend the v2.3 model with v3.0 observations and anomaly codes."""

import sqlalchemy as sa
from alembic import op

revision = "0004_model_v30"
down_revision = "0003_model_v23"
branch_labels = None
depends_on = None

_CODES = (
    "'NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', 'STATUS_MISMATCH', "
    "'DUPLICATE_DNS', 'DUPLICATE_IP', 'RENAMED', 'IP_OUT_OF_PREFIX', "
    "'VM_IP_CONFLICT', 'DNS_MISSING_PTR', 'PTR_MISMATCH', 'MISSING_REQUIRED_TAG', "
    "'MISSING_TENANT', 'MISSING_SITE', 'DISK_OVER_90', 'RAM_SATURATED', "
    "'VM_UNDERUSED', 'VM_ZOMBIE', 'NO_RECENT_BACKUP', 'FORGOTTEN_SNAPSHOT'"
)
_OLD_CODES = (
    "'NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', 'STATUS_MISMATCH', "
    "'DUPLICATE_DNS', 'DUPLICATE_IP', 'RENAMED'"
)
_ANOMALY_COLUMNS = (
    "id, run_id, asset_id, ipam_record_id, code, description, created_at, "
    "tracking_key, state, state_changed_at, severity"
)
_EXCEPTION_COLUMNS = "id, code, target, justification, expires_at, author"


def _replace_anomaly(codes, severity_nullable):
    op.execute(f"""CREATE TABLE anomaly_v30 (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
        asset_id INTEGER,
        ipam_record_id INTEGER,
        code TEXT NOT NULL CHECK (code IN ({codes})),
        description TEXT NOT NULL,
        created_at TEXT NOT NULL,
        tracking_key TEXT,
        state TEXT NOT NULL DEFAULT 'ouverte' CHECK (state IN ('ouverte', 'acquittée', 'résolue')),
        state_changed_at TEXT,
        severity TEXT {'' if severity_nullable else 'NOT NULL '}CHECK (severity IN ('info', 'mineure', 'majeure', 'critique')),
        FOREIGN KEY (asset_id, run_id) REFERENCES asset(id, run_id) ON DELETE RESTRICT,
        FOREIGN KEY (ipam_record_id, run_id) REFERENCES ipam_record(id, run_id) ON DELETE RESTRICT
    )""")
    op.execute(f"INSERT INTO anomaly_v30 ({_ANOMALY_COLUMNS}) SELECT {_ANOMALY_COLUMNS} FROM anomaly")
    op.drop_table("anomaly")
    op.rename_table("anomaly_v30", "anomaly")
    for name, columns in (
        ("idx_anomaly_run", ["run_id"]),
        ("idx_anomaly_asset_run", ["asset_id", "run_id"]),
        ("idx_anomaly_ipam_run", ["ipam_record_id", "run_id"]),
        ("idx_anomaly_tracking", ["tracking_key", "created_at"]),
        ("idx_anomaly_severity", ["severity"]),
    ):
        op.create_index(name, "anomaly", columns)


def _replace_exception(codes):
    op.execute(f"""CREATE TABLE anomaly_exception_v30 (
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL CHECK (code IN ({codes})),
        target TEXT NOT NULL CHECK (length(trim(target)) > 0),
        justification TEXT NOT NULL CHECK (length(trim(justification)) > 0),
        expires_at TEXT NOT NULL,
        author TEXT NOT NULL CHECK (length(trim(author)) > 0)
    )""")
    op.execute(f"INSERT INTO anomaly_exception_v30 ({_EXCEPTION_COLUMNS}) SELECT {_EXCEPTION_COLUMNS} FROM anomaly_exception")
    op.drop_table("anomaly_exception")
    op.rename_table("anomaly_exception_v30", "anomaly_exception")


def upgrade():
    for name, type_ in (
        ("disk_usage_percent", sa.REAL()), ("ram_usage_percent", sa.REAL()),
        ("vm_stopped_since", sa.Text()), ("backup_collection_status", sa.Text()),
    ):
        checks = {
            "disk_usage_percent": "disk_usage_percent BETWEEN 0 AND 100",
            "ram_usage_percent": "ram_usage_percent BETWEEN 0 AND 100",
            "backup_collection_status": "backup_collection_status IN ('inconnue', 'réussie', 'échouée')",
        }
        op.add_column("asset", sa.Column(name, type_, sa.CheckConstraint(checks[name]) if name in checks else None))
    for name in ("network_prefix", "ptr_name"):
        op.add_column("ipam_record", sa.Column(name, sa.Text()))
    for name, check in (
        ("match_confidence", "match_confidence BETWEEN 0 AND 100"),
        ("match_doubt", "match_doubt IN (0, 1)"),
    ):
        op.add_column("consolidated_asset", sa.Column(name, sa.Integer(), sa.CheckConstraint(check)))

    _replace_anomaly(_CODES, True)
    _replace_exception(_CODES)

    for table, identifier in (("snapshot_observation", "snapshot"), ("backup_observation", "backup")):
        op.execute(f"""CREATE TABLE {table} (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
            asset_id INTEGER NOT NULL,
            {identifier}_id TEXT,
            {identifier}_created_at TEXT,
            FOREIGN KEY (asset_id, run_id) REFERENCES asset(id, run_id) ON DELETE CASCADE
        )""")
        op.create_index(f"idx_{identifier}_run", table, ["run_id"])
        op.create_index(f"idx_{identifier}_asset_run", table, ["asset_id", "run_id"])
    op.create_table(
        "node_capacity_observation",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_capacity_name", sa.Text(), nullable=False),
        sa.Column("node_capacity_measured_at", sa.Text()),
        sa.Column("node_capacity_value", sa.REAL()),
        sa.Column("node_capacity_limit", sa.REAL()),
        sa.CheckConstraint("length(trim(node_capacity_name)) > 0"),
    )
    op.create_index("idx_node_capacity_run_name", "node_capacity_observation", ["run_id", "node_capacity_name"])


def downgrade():
    op.drop_index("idx_node_capacity_run_name", table_name="node_capacity_observation")
    op.drop_table("node_capacity_observation")
    for table, identifier in (("backup_observation", "backup"), ("snapshot_observation", "snapshot")):
        op.drop_index(f"idx_{identifier}_asset_run", table_name=table)
        op.drop_index(f"idx_{identifier}_run", table_name=table)
        op.drop_table(table)
    _replace_exception(_OLD_CODES)
    _replace_anomaly(_OLD_CODES, False)
    with op.batch_alter_table("consolidated_asset") as batch:
        batch.drop_column("match_doubt")
        batch.drop_column("match_confidence")
    with op.batch_alter_table("ipam_record") as batch:
        batch.drop_column("ptr_name")
        batch.drop_column("network_prefix")
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("backup_collection_status")
        batch.drop_column("vm_stopped_since")
        batch.drop_column("ram_usage_percent")
        batch.drop_column("disk_usage_percent")
