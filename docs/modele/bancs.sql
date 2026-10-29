-- attendu : accepté
INSERT INTO run(id,status,started_at) VALUES (1,'RUNNING','2026-01-01'),(2,'RUNNING','2026-01-02');
-- attendu : accepté
INSERT INTO asset(id,run_id,vm_id) VALUES (1,1,'same-vm'),(2,2,'same-vm');
-- attendu : accepté
INSERT INTO ipam_record(id,run_id,ip,dns_name,meta_zone) VALUES (1,1,'10.0.0.1','same.test','zone-a'),(2,2,'10.0.0.1','same.test','zone-a'),(3,1,'10.0.0.1','same.test','zone-b');
-- attendu : accepté
INSERT INTO consolidated_asset(run_id,asset_id,match_status) VALUES (1,1,'NO_MATCH');
-- attendu : accepté
INSERT INTO consolidated_asset(run_id,asset_id,ipam_record_id,match_status) VALUES (2,2,2,'MATCHED_IP');
-- attendu : accepté
INSERT INTO anomaly(run_id,asset_id,ipam_record_id,code,description,created_at,severity) VALUES (1,1,1,'NO_MATCH','test','2026-01-01','info'),(1,1,1,'MATCHED_IP','test','2026-01-01','info'),(1,1,1,'HOSTNAME_MISMATCH','test','2026-01-01','info'),(1,1,1,'STATUS_MISMATCH','test','2026-01-01','info'),(1,1,1,'DUPLICATE_DNS','test','2026-01-01','info'),(1,1,1,'DUPLICATE_IP','test','2026-01-01','info');
-- attendu : 1 ligne(s)
SELECT 1 WHERE (SELECT count(*) FROM asset WHERE vm_id='same-vm')=2 AND (SELECT count(*) FROM ipam_record WHERE run_id=1 AND ip='10.0.0.1' AND dns_name='same.test')=2 AND (SELECT count(DISTINCT meta_zone) FROM ipam_record WHERE run_id=1)=2 AND (SELECT count(*) FROM consolidated_asset WHERE ipam_record_id IS NULL)=1 AND (SELECT count(DISTINCT code) FROM anomaly)=6;
-- attendu : refus
INSERT INTO anomaly(run_id,code,description,created_at,severity) VALUES (1,'UNKNOWN','test','2026-01-01','info');
-- attendu : refus
INSERT INTO consolidated_asset(run_id,asset_id,match_status) VALUES (2,1,'NO_MATCH');
-- attendu : refus
UPDATE consolidated_asset SET ipam_record_id=2 WHERE run_id=1 AND asset_id=1;
-- attendu : refus
INSERT INTO anomaly(run_id,asset_id,code,description,created_at,severity) VALUES (2,1,'NO_MATCH','test','2026-01-01','info');
-- attendu : refus
INSERT INTO anomaly(run_id,ipam_record_id,code,description,created_at,severity) VALUES (2,1,'NO_MATCH','test','2026-01-01','info');
-- attendu : accepté
INSERT INTO anomaly(run_id,code,description,created_at,severity,tracking_key) VALUES (2,'RENAMED','renommage','2026-01-02','mineure','vm-2');
-- attendu : refus
INSERT INTO anomaly(run_id,code,description,created_at,severity) VALUES (2,'RENAMED','test','2026-01-02','inconnue');
-- attendu : accepté
INSERT INTO anomaly_exception(code,target,justification,expires_at,author) VALUES ('RENAMED','vm-2','justification','2026-12-31','admin');
-- attendu : refus
INSERT INTO anomaly_exception(code,target,justification,expires_at,author) VALUES ('RENAMED','vm-2',' ','2026-12-31','admin');
-- attendu : accepté
INSERT INTO run_schedule(frequency,enabled) VALUES ('horaire',1);
-- attendu : accepté
UPDATE run SET schedule_id=1,run_trigger='planifié',current_step=7,completed_steps=7 WHERE id=2;
-- attendu : refus
UPDATE run SET current_step=8 WHERE id=2;
-- attendu : refus
INSERT INTO run_schedule(frequency) VALUES ('hebdomadaire');
-- attendu : 1 ligne(s)
SELECT 1 WHERE (SELECT count(*) FROM anomaly WHERE code='RENAMED' AND severity='mineure' AND tracking_key='vm-2')=1 AND (SELECT count(*) FROM run WHERE schedule_id=1 AND completed_steps=7)=1 AND (SELECT count(*) FROM anomaly_exception WHERE code='RENAMED')=1 AND (SELECT count(*) FROM pragma_integrity_check WHERE integrity_check='ok')=1 AND (SELECT count(*) FROM pragma_foreign_key_check)=0;
-- attendu : accepté
DELETE FROM run WHERE id=1;
-- attendu : 1 ligne(s)
SELECT 1 WHERE (SELECT count(*) FROM run WHERE id=1)=0 AND (SELECT count(*) FROM asset WHERE run_id=1)=0 AND (SELECT count(*) FROM ipam_record WHERE run_id=1)=0 AND (SELECT count(*) FROM consolidated_asset WHERE run_id=1)=0 AND (SELECT count(*) FROM anomaly WHERE run_id=1)=0 AND (SELECT count(*) FROM run WHERE id=2)=1 AND (SELECT count(*) FROM asset WHERE run_id=2)=1 AND (SELECT count(*) FROM ipam_record WHERE run_id=2)=1 AND (SELECT count(*) FROM consolidated_asset WHERE run_id=2)=1 AND (SELECT count(*) FROM pragma_foreign_key_check)=0;
-- attendu : accepté
INSERT INTO auth_origin(origin_code) VALUES ('local'),('LDAP'),('AD');
-- attendu : accepté
INSERT INTO role(role_code) VALUES ('admin'),('opérateur'),('lecteur');
-- attendu : accepté
INSERT INTO permission(permission_code) VALUES ('launch'),('read');
-- attendu : accepté
INSERT INTO account(id,origin_code,role_code,username,local_password_hash) VALUES (1,'local','admin','alex','hash-example'),(2,'LDAP','lecteur','alex',NULL),(3,'AD','opérateur','alex',NULL);
-- attendu : accepté
INSERT INTO role_permission(role_code,permission_code) VALUES ('admin','launch');
-- attendu : accepté
INSERT INTO audit(account_id,actor_identity,action,created_at,target) VALUES (1,'local:alex','purge','2026-01-03','run:1');
-- attendu : accepté
UPDATE asset SET virtual_source='vSphere' WHERE id=2;
-- attendu : accepté
UPDATE ipam_record SET ipam_source='EfficientIP' WHERE id=2;
-- attendu : 1 ligne(s)
SELECT 1 WHERE (SELECT count(*) FROM account WHERE username='alex')=3 AND (SELECT count(*) FROM account WHERE local_password_hash IS NULL)=2 AND (SELECT count(*) FROM role_permission)=1 AND (SELECT count(*) FROM audit WHERE target='run:1')=1 AND (SELECT virtual_source FROM asset WHERE id=2)='vSphere' AND (SELECT ipam_source FROM ipam_record WHERE id=2)='EfficientIP';
-- attendu : refus
INSERT INTO account(origin_code,role_code,username,local_password_hash) VALUES ('LDAP','lecteur','other','forbidden');
-- attendu : refus
INSERT INTO account(origin_code,role_code,username) VALUES ('local','admin','other');
-- attendu : refus
INSERT INTO account(origin_code,role_code,username) VALUES ('LDAP','lecteur','alex');
-- attendu : refus
INSERT INTO role_permission(role_code,permission_code) VALUES ('lecteur','missing');
-- attendu : refus
INSERT INTO role_permission(role_code,permission_code) VALUES ('admin','launch');
-- attendu : refus
INSERT INTO audit(actor_identity,action,created_at,target) VALUES (' ','purge','2026-01-03','run:1');
-- attendu : accepté
DELETE FROM account WHERE id=1;
-- attendu : 1 ligne(s)
SELECT 1 WHERE (SELECT count(*) FROM audit WHERE account_id IS NULL AND actor_identity='local:alex' AND target='run:1')=1 AND (SELECT count(*) FROM pragma_foreign_key_check)=0 AND (SELECT count(*) FROM pragma_integrity_check WHERE integrity_check='ok')=1;
