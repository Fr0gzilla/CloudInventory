-- schema.sql — CloudInventory v2.0, DDL des 5 tables du MLD
-- Source   : docs/modele/mld.md (5 tables, 44 colonnes, 6 FK, « Ordre de création »),
--            docs/modele/classes.md, docs/modele/dictionnaire.md (types et longueurs),
--            docs/modele/mcd.md (cardinalités des 6 associations)
-- Dialecte : SQLite (base de données SQLite intégrée, cahier des charges)
-- Date     : 2026-10-07
-- Hors script : aucune donnée, aucun secret.
-- Ordre des tables : run, ipam_record, asset, consolidated_asset, anomaly —
--   chaque table est créée après toutes celles qu'elle référence (aucun cycle de FK).

PRAGMA foreign_keys = ON;

-- 1. run — aucune FK (Entité Run ; RG17, RG19, RG20)
CREATE TABLE `run` (
  `id` INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  `status` VARCHAR(20) NOT NULL CHECK (`status` IN ('RUNNING','SUCCESS','FAIL')),
  `start_date` DATETIME NOT NULL,
  `end_date` DATETIME,
  `matched_name_count` INTEGER,
  `matched_fqdn_count` INTEGER,
  `matched_ip_count` INTEGER,
  `no_match_count` INTEGER,
  `error_message` TEXT
);
CREATE INDEX `idx_run_status` ON `run` (`status`);
CREATE INDEX `idx_run_start_date` ON `run` (`start_date`);

-- 2. ipam_record — aucune FK (Entité IpamRecord)
--    uq_ipam_record_ip_dns : clé d'upsert RG18 (ip + dns_name) ; ip seule n'est pas unique, RG13 détecte les doublons (mld.md, Écart 1).
CREATE TABLE `ipam_record` (
  `id` INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  `ip` VARCHAR(45) NOT NULL,
  `dns_name` VARCHAR(200) NOT NULL,
  `tenant` VARCHAR(50),
  `site` VARCHAR(50),
  `tags` TEXT,
  `is_duplicate_dns` BOOLEAN DEFAULT FALSE,
  `is_duplicate_ip` BOOLEAN DEFAULT FALSE
);
CREATE UNIQUE INDEX `uq_ipam_record_ip_dns` ON `ipam_record` (`ip`, `dns_name`);
CREATE INDEX `idx_ipam_record_dns_name` ON `ipam_record` (`dns_name`);

-- 3. asset — FK → run : association produire, Run (0,n) – Asset (1,1) ;
--    FK NOT NULL (min = 1 : chaque asset appartient à exactement un run, RG18/RG20), ON DELETE RESTRICT.
--    uk_asset_vm_id : clé d'upsert RG18 (vm_id).
CREATE TABLE `asset` (
  `id` INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  `vm_id` VARCHAR(50) NOT NULL,
  `vm_name` VARCHAR(100) NOT NULL,
  `fqdn` VARCHAR(200),
  `ip_reported` VARCHAR(45),
  `node` VARCHAR(10) CHECK (`node` IN ('pve1','pve2','pve3','pve4','pve5')),
  `type` VARCHAR(10) CHECK (`type` IN ('qemu','lxc')),
  `status` VARCHAR(20) CHECK (`status` IN ('running','stopped')),
  `tags` TEXT,
  `role` VARCHAR(50),
  `match_status` VARCHAR(20) CHECK (`match_status` IN ('MATCHED_NAME','MATCHED_FQDN','MATCHED_IP','NO_MATCH')),
  `source` VARCHAR(10) CHECK (`source` IN ('VIRT','IPAM')),
  `consolidated_run_id` INTEGER NOT NULL,
  `os` VARCHAR(100) NULL,
  `annotation` TEXT NULL,
  `cpu_count` INT NULL,
  `cpu_usage` FLOAT NULL,
  `ram_max` BIGINT NULL,
  `ram_used` BIGINT NULL,
  `disk_max` BIGINT NULL,
  `disk_used` BIGINT NULL,
  `uptime` INT NULL,
  FOREIGN KEY (`consolidated_run_id`) REFERENCES `run` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
);
CREATE UNIQUE INDEX `uk_asset_vm_id` ON `asset` (`vm_id`);
CREATE INDEX `idx_asset_consolidated_run_id` ON `asset` (`consolidated_run_id`);
CREATE INDEX `idx_asset_vm_name` ON `asset` (`vm_name`);
CREATE INDEX `idx_asset_fqdn` ON `asset` (`fqdn`);
CREATE INDEX `idx_asset_ip_reported` ON `asset` (`ip_reported`);
CREATE INDEX `idx_asset_status` ON `asset` (`status`);
CREATE INDEX `idx_asset_node` ON `asset` (`node`);
CREATE INDEX `idx_asset_type` ON `asset` (`type`);
CREATE INDEX `idx_asset_match_status` ON `asset` (`match_status`);
CREATE INDEX `idx_asset_os` ON `asset` (`os`);
CREATE INDEX `idx_asset_annotation` ON `asset` (`annotation`);
CREATE INDEX `idx_asset_cpu_count` ON `asset` (`cpu_count`);
CREATE INDEX `idx_asset_cpu_usage` ON `asset` (`cpu_usage`);
CREATE INDEX `idx_asset_ram_max` ON `asset` (`ram_max`);
CREATE INDEX `idx_asset_ram_used` ON `asset` (`ram_used`);
CREATE INDEX `idx_asset_disk_max` ON `asset` (`disk_max`);
CREATE INDEX `idx_asset_disk_used` ON `asset` (`disk_used`);
CREATE INDEX `idx_asset_uptime` ON `asset` (`uptime`);

-- 4. consolidated_asset — FK → asset : association consolider, Asset (1,n) – ConsolidatedAsset (1,1) ;
--    NOT NULL (min = 1), ON DELETE CASCADE (lignes dérivées d'un asset, RG18).
--    FK → run : historique et comparaison de runs (OF9, §8.5-8.6) ;
--    NOT NULL car chaque asset consolidé rattache directement un run, RG35/RG36.
--    FK → ipam_record : association renseigner, IpamRecord (0,n) – ConsolidatedAsset (0,1) ;
--    NULL si NO_MATCH (min = 0, RG05/RG08), ON DELETE RESTRICT.
CREATE TABLE `consolidated_asset` (
  `id` INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  `run_id` INTEGER NOT NULL,
  `asset_id` INTEGER NOT NULL,
  `ipam_record_id` INTEGER,
  `match_status` VARCHAR(20) NOT NULL CHECK (`match_status` IN ('MATCHED_NAME','MATCHED_FQDN','MATCHED_IP','NO_MATCH')),
  `role` VARCHAR(50),
  `anomaly_codes` TEXT,
  `consolidated_at` DATETIME,
  `ip_final` VARCHAR(45) NULL,
  `dns_final` VARCHAR(200) NULL,
  `vm_status` VARCHAR(20) NULL,
  FOREIGN KEY (`run_id`) REFERENCES `run` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  FOREIGN KEY (`asset_id`) REFERENCES `asset` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  FOREIGN KEY (`ipam_record_id`) REFERENCES `ipam_record` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
);
CREATE INDEX `idx_consolidated_asset_asset_id` ON `consolidated_asset` (`asset_id`);
CREATE INDEX `idx_consolidated_asset_run_id` ON `consolidated_asset` (`run_id`);
CREATE INDEX `idx_consolidated_asset_ipam_record_id` ON `consolidated_asset` (`ipam_record_id`);
CREATE INDEX `idx_consolidated_asset_match_status` ON `consolidated_asset` (`match_status`);
CREATE INDEX `idx_consolidated_asset_ip_final` ON `consolidated_asset` (`ip_final`);
CREATE INDEX `idx_consolidated_asset_dns_final` ON `consolidated_asset` (`dns_final`);
CREATE INDEX `idx_consolidated_asset_vm_status` ON `consolidated_asset` (`vm_status`);

-- 5. anomaly — FK → run : association detecter, Run (0,n) – Anomaly (1,1), NOT NULL (min = 1), ON DELETE RESTRICT.
--    FK → asset : association signaler, Asset (0,n) – Anomaly (0,1), NULL (min = 0), ON DELETE RESTRICT.
--    FK → ipam_record : association concerner, IpamRecord (0,n) – Anomaly (0,1), NULL (min = 0), ON DELETE RESTRICT.
CREATE TABLE `anomaly` (
  `id` INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  `run_id` INTEGER NOT NULL,
  `asset_id` INTEGER,
  `ipam_record_id` INTEGER,
  `code` VARCHAR(20) NOT NULL CHECK (`code` IN ('NO_MATCH','HOSTNAME_MISMATCH','STATUS_MISMATCH','DUPLICATE_DNS','DUPLICATE_IP')),
  `description` TEXT,
  `detected_at` DATETIME NOT NULL,
  FOREIGN KEY (`run_id`) REFERENCES `run` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  FOREIGN KEY (`asset_id`) REFERENCES `asset` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE,
  FOREIGN KEY (`ipam_record_id`) REFERENCES `ipam_record` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
);
CREATE INDEX `idx_anomaly_run_id` ON `anomaly` (`run_id`);
CREATE INDEX `idx_anomaly_asset_id` ON `anomaly` (`asset_id`);
CREATE INDEX `idx_anomaly_ipam_record_id` ON `anomaly` (`ipam_record_id`);
CREATE INDEX `idx_anomaly_code` ON `anomaly` (`code`);
