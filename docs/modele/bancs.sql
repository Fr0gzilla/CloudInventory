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
INSERT INTO anomaly(run_id,asset_id,ipam_record_id,code,description,created_at) VALUES (1,1,1,'NO_MATCH','test','2026-01-01'),(1,1,1,'MATCHED_IP','test','2026-01-01'),(1,1,1,'HOSTNAME_MISMATCH','test','2026-01-01'),(1,1,1,'STATUS_MISMATCH','test','2026-01-01'),(1,1,1,'DUPLICATE_DNS','test','2026-01-01'),(1,1,1,'DUPLICATE_IP','test','2026-01-01');
-- attendu : 1 ligne(s)
SELECT 1 WHERE (SELECT count(*) FROM asset WHERE vm_id='same-vm')=2 AND (SELECT count(*) FROM ipam_record WHERE run_id=1 AND ip='10.0.0.1' AND dns_name='same.test')=2 AND (SELECT count(DISTINCT meta_zone) FROM ipam_record WHERE run_id=1)=2 AND (SELECT count(*) FROM consolidated_asset WHERE ipam_record_id IS NULL)=1 AND (SELECT count(DISTINCT code) FROM anomaly)=6;
-- attendu : refus
INSERT INTO anomaly(run_id,code,description,created_at) VALUES (1,'UNKNOWN','test','2026-01-01');
-- attendu : refus
INSERT INTO consolidated_asset(run_id,asset_id,match_status) VALUES (2,1,'NO_MATCH');
-- attendu : refus
UPDATE consolidated_asset SET ipam_record_id=2 WHERE run_id=1 AND asset_id=1;
-- attendu : refus
INSERT INTO anomaly(run_id,asset_id,code,description,created_at) VALUES (2,1,'NO_MATCH','test','2026-01-01');
-- attendu : refus
INSERT INTO anomaly(run_id,ipam_record_id,code,description,created_at) VALUES (2,1,'NO_MATCH','test','2026-01-01');
-- attendu : accepté
DELETE FROM run WHERE id=1;
-- attendu : 1 ligne(s)
SELECT 1 WHERE (SELECT count(*) FROM run WHERE id=1)=0 AND (SELECT count(*) FROM asset WHERE run_id=1)=0 AND (SELECT count(*) FROM ipam_record WHERE run_id=1)=0 AND (SELECT count(*) FROM consolidated_asset WHERE run_id=1)=0 AND (SELECT count(*) FROM anomaly WHERE run_id=1)=0 AND (SELECT count(*) FROM run WHERE id=2)=1 AND (SELECT count(*) FROM asset WHERE run_id=2)=1 AND (SELECT count(*) FROM ipam_record WHERE run_id=2)=1 AND (SELECT count(*) FROM consolidated_asset WHERE run_id=2)=1 AND (SELECT count(*) FROM pragma_foreign_key_check)=0;
