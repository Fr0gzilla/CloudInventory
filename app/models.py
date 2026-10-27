from app import db


class Run(db.Model):
    __tablename__ = "run"

    id = db.Column(db.Integer, primary_key=True, nullable=True)
    status = db.Column(db.Text, nullable=False)
    started_at = db.Column(db.Text, nullable=False)
    finished_at = db.Column(db.Text)
    matched_name_count = db.Column(db.Integer)
    matched_fqdn_count = db.Column(db.Integer)
    matched_ip_count = db.Column(db.Integer)
    no_match_count = db.Column(db.Integer)
    error_message = db.Column(db.Text)

    __table_args__ = (
        db.CheckConstraint("status IN ('RUNNING', 'SUCCESS', 'FAIL')"),
        db.CheckConstraint("matched_name_count >= 0"),
        db.CheckConstraint("matched_fqdn_count >= 0"),
        db.CheckConstraint("matched_ip_count >= 0"),
        db.CheckConstraint("no_match_count >= 0"),
    )


class Asset(db.Model):
    __tablename__ = "asset"

    id = db.Column(db.Integer, primary_key=True, nullable=True)
    run_id = db.Column(db.Integer, db.ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    vm_id = db.Column(db.Text, nullable=False)
    vm_name = db.Column(db.Text)
    fqdn = db.Column(db.Text)
    ip_reported = db.Column(db.Text)
    node = db.Column(db.Text)
    type = db.Column(db.Text)
    status = db.Column(db.Text)
    tags = db.Column(db.Text)
    role = db.Column(db.Text)
    os = db.Column(db.Text)
    annotation = db.Column(db.Text)
    cpu = db.Column(db.REAL)
    ram = db.Column(db.REAL)
    disk = db.Column(db.REAL)
    uptime = db.Column(db.REAL)

    __table_args__ = (
        db.CheckConstraint("type IN ('qemu', 'lxc')"),
        db.UniqueConstraint("id", "run_id"),
        db.Index("idx_asset_run", "run_id"),
        db.Index("idx_asset_run_vm", "run_id", "vm_id"),
        db.Index("idx_asset_run_name", "run_id", "vm_name"),
        db.Index("idx_asset_run_fqdn", "run_id", "fqdn"),
        db.Index("idx_asset_run_ip", "run_id", "ip_reported"),
        db.Index("idx_asset_run_node", "run_id", "node"),
    )


class IpamRecord(db.Model):
    __tablename__ = "ipam_record"

    id = db.Column(db.Integer, primary_key=True, nullable=True)
    run_id = db.Column(db.Integer, db.ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    ip = db.Column(db.Text)
    dns_name = db.Column(db.Text)
    status = db.Column(db.Text)
    tenant = db.Column(db.Text)
    site = db.Column(db.Text)
    tags = db.Column(db.Text)
    duplicate_dns = db.Column(db.Integer, nullable=False, server_default=db.text("0"))
    duplicate_ip = db.Column(db.Integer, nullable=False, server_default=db.text("0"))
    meta_zone = db.Column(db.Text)

    __table_args__ = (
        db.CheckConstraint("duplicate_dns IN (0, 1)"),
        db.CheckConstraint("duplicate_ip IN (0, 1)"),
        db.UniqueConstraint("id", "run_id"),
        db.Index("idx_ipam_run_ip", "run_id", "ip"),
        db.Index("idx_ipam_run_dns", "run_id", "dns_name"),
    )


class ConsolidatedAsset(db.Model):
    __tablename__ = "consolidated_asset"

    id = db.Column(db.Integer, primary_key=True, nullable=True)
    run_id = db.Column(db.Integer, nullable=False)
    asset_id = db.Column(db.Integer, nullable=False)
    ipam_record_id = db.Column(db.Integer)
    ip_final = db.Column(db.Text)
    dns_final = db.Column(db.Text)
    status_final = db.Column(db.Text)
    match_status = db.Column(db.Text, nullable=False)
    role = db.Column(db.Text)

    __table_args__ = (
        db.CheckConstraint(
            "match_status IN ('MATCHED_NAME', 'MATCHED_FQDN', 'MATCHED_IP', 'NO_MATCH')"
        ),
        db.ForeignKeyConstraint(["run_id"], ["run.id"], ondelete="CASCADE"),
        db.ForeignKeyConstraint(["asset_id", "run_id"], ["asset.id", "asset.run_id"], ondelete="CASCADE"),
        db.ForeignKeyConstraint(
            ["ipam_record_id", "run_id"], ["ipam_record.id", "ipam_record.run_id"],
            ondelete="RESTRICT",
        ),
        db.UniqueConstraint("asset_id", "run_id"),
        db.Index("idx_consolidated_run", "run_id"),
        db.Index("idx_consolidated_ipam_run", "ipam_record_id", "run_id"),
        db.Index("idx_consolidated_run_match", "run_id", "match_status"),
    )


class Anomaly(db.Model):
    __tablename__ = "anomaly"

    id = db.Column(db.Integer, primary_key=True, nullable=True)
    run_id = db.Column(db.Integer, nullable=False)
    asset_id = db.Column(db.Integer)
    ipam_record_id = db.Column(db.Integer)
    code = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Text, nullable=False)

    __table_args__ = (
        db.CheckConstraint(
            "code IN ('NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', 'STATUS_MISMATCH', "
            "'DUPLICATE_DNS', 'DUPLICATE_IP')"
        ),
        db.ForeignKeyConstraint(["run_id"], ["run.id"], ondelete="CASCADE"),
        db.ForeignKeyConstraint(["asset_id", "run_id"], ["asset.id", "asset.run_id"], ondelete="RESTRICT"),
        db.ForeignKeyConstraint(
            ["ipam_record_id", "run_id"], ["ipam_record.id", "ipam_record.run_id"],
            ondelete="RESTRICT",
        ),
        db.Index("idx_anomaly_run", "run_id"),
        db.Index("idx_anomaly_asset_run", "asset_id", "run_id"),
        db.Index("idx_anomaly_ipam_run", "ipam_record_id", "run_id"),
    )
