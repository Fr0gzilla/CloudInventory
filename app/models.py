"""Modèles SQLAlchemy — mapping 1:1 de docs/modele/schema.sql (5 tables, 6 FK)."""
from app.extensions import db


class Run(db.Model):
    __tablename__ = "run"

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=True)
    matched_name_count = db.Column(db.Integer, nullable=True)
    matched_fqdn_count = db.Column(db.Integer, nullable=True)
    matched_ip_count = db.Column(db.Integer, nullable=True)
    no_match_count = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    assets = db.relationship("Asset", back_populates="run", lazy=True)
    anomalies = db.relationship("Anomaly", back_populates="run", lazy=True)
    # Pas de FK directe : chaîne run → asset → consolidated_asset (schema.sql).
    consolidated_assets = db.relationship(
        "ConsolidatedAsset",
        primaryjoin="Run.id == Asset.consolidated_run_id",
        secondaryjoin="Asset.id == ConsolidatedAsset.asset_id",
        secondary="asset",
        viewonly=True,
        lazy=True,
    )

    __table_args__ = (
        db.CheckConstraint("status IN ('RUNNING','SUCCESS','FAIL')"),
        db.Index("idx_run_status", "status"),
        db.Index("idx_run_start_date", "start_date"),
        {"sqlite_autoincrement": True},
    )


class IpamRecord(db.Model):
    __tablename__ = "ipam_record"

    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(45), nullable=False)
    dns_name = db.Column(db.String(200), nullable=False)
    tenant = db.Column(db.String(50), nullable=True)
    site = db.Column(db.String(50), nullable=True)
    tags = db.Column(db.Text, nullable=True)
    is_duplicate_dns = db.Column(
        db.Boolean, default=False, server_default=db.text("FALSE")
    )
    is_duplicate_ip = db.Column(
        db.Boolean, default=False, server_default=db.text("FALSE")
    )

    consolidated_assets = db.relationship(
        "ConsolidatedAsset", back_populates="ipam_record", lazy=True
    )

    __table_args__ = (
        db.Index("uq_ipam_record_ip_dns", "ip", "dns_name", unique=True),
        db.Index("idx_ipam_record_dns_name", "dns_name"),
        {"sqlite_autoincrement": True},
    )


class Asset(db.Model):
    __tablename__ = "asset"

    id = db.Column(db.Integer, primary_key=True)
    vm_id = db.Column(db.String(50), nullable=False)
    vm_name = db.Column(db.String(100), nullable=False)
    fqdn = db.Column(db.String(200), nullable=True)
    ip_reported = db.Column(db.String(45), nullable=True)
    node = db.Column(db.String(10), nullable=True)
    type = db.Column(db.String(10), nullable=True)
    status = db.Column(db.String(20), nullable=True)
    tags = db.Column(db.Text, nullable=True)
    role = db.Column(db.String(50), nullable=True)
    match_status = db.Column(db.String(20), nullable=True)
    source = db.Column(db.String(10), nullable=True)
    consolidated_run_id = db.Column(
        db.Integer,
        db.ForeignKey("run.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )

    run = db.relationship("Run", back_populates="assets", lazy=True)
    consolidated_assets = db.relationship(
        "ConsolidatedAsset",
        back_populates="asset",
        cascade="all, delete-orphan",
        lazy=True,
    )
    anomalies = db.relationship("Anomaly", back_populates="asset", lazy=True)

    __table_args__ = (
        db.CheckConstraint("node IN ('pve1','pve2','pve3','pve4','pve5')"),
        db.CheckConstraint("type IN ('qemu','lxc')"),
        db.CheckConstraint("status IN ('running','stopped')"),
        db.CheckConstraint(
            "match_status IN ('MATCHED_NAME','MATCHED_FQDN','MATCHED_IP','NO_MATCH')"
        ),
        db.CheckConstraint("source IN ('VIRT','IPAM')"),
        db.Index("uk_asset_vm_id", "vm_id", unique=True),
        db.Index("idx_asset_consolidated_run_id", "consolidated_run_id"),
        db.Index("idx_asset_vm_name", "vm_name"),
        db.Index("idx_asset_fqdn", "fqdn"),
        db.Index("idx_asset_ip_reported", "ip_reported"),
        db.Index("idx_asset_status", "status"),
        db.Index("idx_asset_node", "node"),
        db.Index("idx_asset_type", "type"),
        db.Index("idx_asset_match_status", "match_status"),
        {"sqlite_autoincrement": True},
    )


class ConsolidatedAsset(db.Model):
    __tablename__ = "consolidated_asset"

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    ipam_record_id = db.Column(
        db.Integer,
        db.ForeignKey("ipam_record.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=True,
    )
    match_status = db.Column(db.String(20), nullable=False)
    role = db.Column(db.String(50), nullable=True)
    anomaly_codes = db.Column(db.Text, nullable=True)
    consolidated_at = db.Column(db.DateTime, nullable=True)

    asset = db.relationship("Asset", back_populates="consolidated_assets", lazy=True)
    ipam_record = db.relationship(
        "IpamRecord", back_populates="consolidated_assets", lazy=True
    )

    __table_args__ = (
        db.CheckConstraint(
            "match_status IN ('MATCHED_NAME','MATCHED_FQDN','MATCHED_IP','NO_MATCH')"
        ),
        db.Index("idx_consolidated_asset_asset_id", "asset_id"),
        db.Index("idx_consolidated_asset_ipam_record_id", "ipam_record_id"),
        db.Index("idx_consolidated_asset_match_status", "match_status"),
        {"sqlite_autoincrement": True},
    )


class Anomaly(db.Model):
    __tablename__ = "anomaly"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(
        db.Integer,
        db.ForeignKey("run.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=True,
    )
    ipam_record_id = db.Column(
        db.Integer,
        db.ForeignKey("ipam_record.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=True,
    )
    code = db.Column(db.String(20), nullable=False)
    description = db.Column(db.Text, nullable=True)
    detected_at = db.Column(db.DateTime, nullable=False)

    run = db.relationship("Run", back_populates="anomalies", lazy=True)
    asset = db.relationship("Asset", back_populates="anomalies", lazy=True)
    ipam_record = db.relationship("IpamRecord", lazy=True)

    __table_args__ = (
        db.CheckConstraint(
            "code IN ('NO_MATCH','MATCHED_IP','HOSTNAME_MISMATCH',"
            "'STATUS_MISMATCH','DUPLICATE_DNS','DUPLICATE_IP')"
        ),
        db.Index("idx_anomaly_run_id", "run_id"),
        db.Index("idx_anomaly_asset_id", "asset_id"),
        db.Index("idx_anomaly_ipam_record_id", "ipam_record_id"),
        db.Index("idx_anomaly_code", "code"),
        {"sqlite_autoincrement": True},
    )
