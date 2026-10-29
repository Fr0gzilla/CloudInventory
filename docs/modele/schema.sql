PRAGMA foreign_keys = ON;

CREATE TABLE run_schedule (
    id INTEGER PRIMARY KEY,
    frequency TEXT NOT NULL CHECK (frequency IN ('horaire', 'quotidien')),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    next_at TEXT
);

CREATE TABLE run (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAIL')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    matched_name_count INTEGER CHECK (matched_name_count >= 0),
    matched_fqdn_count INTEGER CHECK (matched_fqdn_count >= 0),
    matched_ip_count INTEGER CHECK (matched_ip_count >= 0),
    no_match_count INTEGER CHECK (no_match_count >= 0),
    error_message TEXT,
    current_step INTEGER CHECK (current_step BETWEEN 1 AND 7),
    completed_steps INTEGER CHECK (completed_steps BETWEEN 0 AND 7),
    run_trigger TEXT CHECK (run_trigger IN ('manuel', 'planifié')),
    schedule_id INTEGER REFERENCES run_schedule(id) ON DELETE SET NULL
);

CREATE TABLE asset (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    vm_id TEXT NOT NULL,
    virtual_source TEXT,
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
    ipam_source TEXT,
    dns_name TEXT,
    status TEXT,
    tenant TEXT,
    site TEXT,
    tags TEXT,
    duplicate_dns INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_dns IN (0, 1)),
    duplicate_ip INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_ip IN (0, 1)),
    meta_zone TEXT,
    custom_fields TEXT,
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
    code TEXT NOT NULL CHECK (code IN ('NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', 'STATUS_MISMATCH', 'DUPLICATE_DNS', 'DUPLICATE_IP', 'RENAMED')),
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tracking_key TEXT,
    state TEXT NOT NULL DEFAULT 'ouverte' CHECK (state IN ('ouverte', 'acquittée', 'résolue')),
    state_changed_at TEXT,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'mineure', 'majeure', 'critique')),
    FOREIGN KEY (asset_id, run_id) REFERENCES asset(id, run_id) ON DELETE RESTRICT,
    FOREIGN KEY (ipam_record_id, run_id) REFERENCES ipam_record(id, run_id) ON DELETE RESTRICT
);

CREATE TABLE anomaly_exception (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL CHECK (code IN ('NO_MATCH', 'MATCHED_IP', 'HOSTNAME_MISMATCH', 'STATUS_MISMATCH', 'DUPLICATE_DNS', 'DUPLICATE_IP', 'RENAMED')),
    target TEXT NOT NULL CHECK (length(trim(target)) > 0),
    justification TEXT NOT NULL CHECK (length(trim(justification)) > 0),
    expires_at TEXT NOT NULL,
    author TEXT NOT NULL CHECK (length(trim(author)) > 0)
);

CREATE INDEX idx_asset_run ON asset(run_id);
CREATE TABLE auth_origin (origin_code TEXT PRIMARY KEY CHECK (origin_code IN ('local', 'LDAP', 'AD')));
CREATE TABLE role (role_code TEXT PRIMARY KEY CHECK (role_code IN ('admin', 'opérateur', 'lecteur')));
CREATE TABLE permission (permission_code TEXT PRIMARY KEY CHECK (length(trim(permission_code)) > 0));
CREATE TABLE account (
    id INTEGER PRIMARY KEY,
    origin_code TEXT NOT NULL REFERENCES auth_origin(origin_code) ON DELETE RESTRICT,
    role_code TEXT NOT NULL REFERENCES role(role_code) ON DELETE RESTRICT,
    username TEXT NOT NULL CHECK (length(trim(username)) > 0),
    local_password_hash TEXT,
    UNIQUE (origin_code, username),
    CHECK ((origin_code = 'local' AND local_password_hash IS NOT NULL AND length(trim(local_password_hash)) > 0)
        OR (origin_code <> 'local' AND local_password_hash IS NULL))
);
CREATE TABLE role_permission (
    id INTEGER PRIMARY KEY,
    role_code TEXT NOT NULL REFERENCES role(role_code) ON DELETE CASCADE,
    permission_code TEXT NOT NULL REFERENCES permission(permission_code) ON DELETE CASCADE,
    UNIQUE (role_code, permission_code)
);
CREATE TABLE audit (
    id INTEGER PRIMARY KEY,
    account_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
    actor_identity TEXT NOT NULL CHECK (length(trim(actor_identity)) > 0),
    action TEXT NOT NULL CHECK (length(trim(action)) > 0),
    created_at TEXT NOT NULL,
    target TEXT NOT NULL CHECK (length(trim(target)) > 0)
);
CREATE INDEX idx_asset_run_vm ON asset(run_id, vm_id);
CREATE INDEX idx_asset_run_name ON asset(run_id, vm_name);
CREATE INDEX idx_asset_run_fqdn ON asset(run_id, fqdn);
CREATE INDEX idx_asset_run_ip ON asset(run_id, ip_reported);
CREATE INDEX idx_asset_run_node ON asset(run_id, node);
CREATE INDEX idx_ipam_run_ip ON ipam_record(run_id, ip);
CREATE INDEX idx_ipam_run_dns ON ipam_record(run_id, dns_name);
CREATE INDEX idx_ipam_run_zone ON ipam_record(run_id, meta_zone);
CREATE INDEX idx_consolidated_run ON consolidated_asset(run_id);
CREATE INDEX idx_consolidated_ipam_run ON consolidated_asset(ipam_record_id, run_id);
CREATE INDEX idx_consolidated_run_match ON consolidated_asset(run_id, match_status);
CREATE INDEX idx_anomaly_run ON anomaly(run_id);
CREATE INDEX idx_anomaly_asset_run ON anomaly(asset_id, run_id);
CREATE INDEX idx_anomaly_ipam_run ON anomaly(ipam_record_id, run_id);
CREATE INDEX idx_run_schedule ON run(schedule_id);
CREATE INDEX idx_anomaly_tracking ON anomaly(tracking_key, created_at);
CREATE INDEX idx_anomaly_severity ON anomaly(severity);
CREATE INDEX idx_account_role ON account(role_code);
CREATE INDEX idx_role_permission_permission ON role_permission(permission_code);
CREATE INDEX idx_audit_account ON audit(account_id);
CREATE INDEX idx_audit_date ON audit(created_at);
