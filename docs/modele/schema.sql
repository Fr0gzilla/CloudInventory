PRAGMA foreign_keys = ON;

CREATE TABLE run (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAIL')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    matched_name_count INTEGER CHECK (matched_name_count >= 0),
    matched_fqdn_count INTEGER CHECK (matched_fqdn_count >= 0),
    matched_ip_count INTEGER CHECK (matched_ip_count >= 0),
    no_match_count INTEGER CHECK (no_match_count >= 0),
    error_message TEXT
);

CREATE TABLE asset (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    vm_id TEXT NOT NULL,
    vm_name TEXT,
    fqdn TEXT,
    ip_reported TEXT,
    node TEXT,
    type TEXT CHECK (type IN ('qemu', 'lxc')),
    status TEXT,
    tags TEXT,
    role TEXT,
    os TEXT,
    annotation TEXT,
    cpu REAL,
    ram REAL,
    disk REAL,
    uptime REAL,
    UNIQUE (id, run_id)
);

CREATE TABLE ipam_record (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    ip TEXT,
    dns_name TEXT,
    status TEXT,
    tenant TEXT,
    site TEXT,
    tags TEXT,
    duplicate_dns INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_dns IN (0, 1)),
    duplicate_ip INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_ip IN (0, 1)),
    meta_zone TEXT,
    UNIQUE (id, run_id)
);

CREATE TABLE consolidated_asset (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL,
    ipam_record_id INTEGER,
    ip_final TEXT,
    dns_final TEXT,
    status_final TEXT,
    match_status TEXT NOT NULL CHECK (match_status IN ('MATCHED_NAME', 'MATCHED_FQDN', 'MATCHED_IP', 'NO_MATCH')),
    role TEXT,
    FOREIGN KEY (asset_id, run_id) REFERENCES asset(id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (ipam_record_id, run_id) REFERENCES ipam_record(id, run_id) ON DELETE RESTRICT,
    UNIQUE (asset_id, run_id)
);

CREATE TABLE anomaly (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    asset_id INTEGER,
    ipam_record_id INTEGER,
    code TEXT NOT NULL CHECK (code IN ('NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', 'STATUS_MISMATCH', 'DUPLICATE_DNS', 'DUPLICATE_IP')),
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (asset_id, run_id) REFERENCES asset(id, run_id) ON DELETE RESTRICT,
    FOREIGN KEY (ipam_record_id, run_id) REFERENCES ipam_record(id, run_id) ON DELETE RESTRICT
);

CREATE INDEX idx_asset_run ON asset(run_id);
CREATE INDEX idx_asset_run_vm ON asset(run_id, vm_id);
CREATE INDEX idx_asset_run_name ON asset(run_id, vm_name);
CREATE INDEX idx_asset_run_fqdn ON asset(run_id, fqdn);
CREATE INDEX idx_asset_run_ip ON asset(run_id, ip_reported);
CREATE INDEX idx_asset_run_node ON asset(run_id, node);
CREATE INDEX idx_ipam_run_ip ON ipam_record(run_id, ip);
CREATE INDEX idx_ipam_run_dns ON ipam_record(run_id, dns_name);
CREATE INDEX idx_consolidated_run ON consolidated_asset(run_id);
CREATE INDEX idx_consolidated_ipam_run ON consolidated_asset(ipam_record_id, run_id);
CREATE INDEX idx_consolidated_run_match ON consolidated_asset(run_id, match_status);
CREATE INDEX idx_anomaly_run ON anomaly(run_id);
CREATE INDEX idx_anomaly_asset_run ON anomaly(asset_id, run_id);
CREATE INDEX idx_anomaly_ipam_run ON anomaly(ipam_record_id, run_id);
