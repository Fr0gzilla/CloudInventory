# Baseline SQL–ORM — EV07/EV59

## État conservé

Les cinq tables `run`, `asset`, `ipam_record`, `consolidated_asset`, `anomaly` existent dans le SQL et l'ORM. Les colonnes et leurs types, nullabilités, valeurs par défaut, CHECK, clés primaires, FK composites et règles ON DELETE correspondent avant EV07 ; `INTEGER PRIMARY KEY` attribue les identifiants sous SQLite (PK ORM `nullable=True`). `ipam_record.meta_zone TEXT NULL` est déjà présent des deux côtés : ne pas le recréer et préserver ses valeurs. Aucun nom IP ni DNS IPAM n'est unique. Les clés candidates `asset(id, run_id)` et `ipam_record(id, run_id)`, ainsi que `consolidated_asset(asset_id, run_id)`, sont déjà définies.

Index déjà présents des deux côtés : `idx_asset_run(run_id)`, `idx_asset_run_vm(run_id, vm_id)`, `idx_asset_run_name(run_id, vm_name)`, `idx_asset_run_fqdn(run_id, fqdn)`, `idx_asset_run_ip(run_id, ip_reported)`, `idx_asset_run_node(run_id, node)` sur `asset` ; `idx_ipam_run_ip(run_id, ip)`, `idx_ipam_run_dns(run_id, dns_name)` sur `ipam_record` ; `idx_consolidated_run(run_id)`, `idx_consolidated_ipam_run(ipam_record_id, run_id)`, `idx_consolidated_run_match(run_id, match_status)` sur `consolidated_asset` ; `idx_anomaly_run(run_id)`, `idx_anomaly_asset_run(asset_id, run_id)`, `idx_anomaly_ipam_run(ipam_record_id, run_id)` sur `anomaly`.

## Évolution additive et écart temporaire

Le SQL cible ajoute uniquement `ipam_record.custom_fields TEXT NULL` (objet JSON complet, NULL si absent, objet vide distinct, sans valeur par défaut ni CHECK sur les clés libres) et `idx_ipam_run_zone(run_id, meta_zone)` sur `ipam_record` ; la zone déjà stockée sert au filtre limité au run. Aucun autre index ou table n'est créé. L'ORM existant ne contient encore ni cette colonne ni cet index : les ajouter à `IpamRecord` avec les mêmes nom, type et nullabilité, et ajouter l'index dans `__table_args__`. Conserver intégralement les autres colonnes, contraintes et index. La collecte doit sérialiser sans perte l'objet reçu pour chaque observation IPAM, même non rapprochée ; absence ≠ `{}`.

Pour les bases existantes, enregistrer d'abord comme baseline versionnée l'état réel des cinq tables et des quatorze index ci-dessus, sans recréer les tables ni effacer les lignes. Une migration ultérieure ajoute la colonne nullable et l'index ; une base neuve doit produire le même schéma cible via SQL et ORM. Comparer les deux schémas après migration : colonne, index, clés et contraintes doivent coïncider. La structure interne des custom fields reste ouverte (Q8) ; aucune indexation du JSON libre.
