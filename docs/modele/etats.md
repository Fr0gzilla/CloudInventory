# Diagramme d'états — Run d'inventaire (CloudInventory v2.0)

## Légende

**Objet** : `Run` (classe de `classes.md`, table `run`). Aucun acteur : c'est un cycle de vie d'objet,
déclenché par la route `POST /run` / `POST /ajax/run` (§8.2).

**Entrées** : création du run (début de pipeline), finalisation sans exception, exception levée pendant le pipeline.
**Sorties** : `status` + `start_date` / `end_date` + 4 compteurs / `error_message` — colonnes de `run`
(`dictionnaire.md`, `schema.sql:16` : `CHECK (status IN ('RUNNING','SUCCESS','FAIL'))`).

**États** : `RUNNING`, `SUCCESS`, `FAIL` — exactement les trois valeurs du dictionnaire (aucun autre statut n'existe).
**Formes Mermaid** : `[*]` initialState / final, `X --> Y : evenement [garde] / action`, `note right of` remarque.
**RG citées** : **RG17** (création `RUNNING`), **RG19** (`SUCCESS` + compteurs), **RG20** (rollback + `FAIL`).

### Diagramme

```mermaid
stateDiagram-v2
    [*] --> RUNNING : creation du run, start_date [RG17]
    RUNNING --> SUCCESS : finalisation sans exception [RG19] / end_date + matched_name_count + matched_fqdn_count + matched_ip_count + no_match_count
    RUNNING --> FAIL : exception pendant le pipeline [RG20] / rollback de la transaction + error_message
    SUCCESS --> [*]
    FAIL --> [*]

    note right of RUNNING : statut impose des la premiere etape du pipeline (RG17) ; les 7 etapes de section 8.2 s'y deroulent
    note right of FAIL : nouveau run necessaire : aucune transition de reprise dans les RG
```

### Traçabilité

| Transition | Événement | Action / données écrites | RG |
|---|---|---|---|
| `[*] → RUNNING` | création du run | `status=RUNNING`, `start_date` | RG17 |
| `RUNNING → SUCCESS` | finalisation sans exception | `status=SUCCESS`, `end_date`, `matched_name_count`, `matched_fqdn_count`, `matched_ip_count`, `no_match_count` | RG19 |
| `RUNNING → FAIL` | exception pendant le pipeline | rollback de la transaction, `status=FAIL`, `error_message` | RG20 |
| `SUCCESS → [*]` | exécution terminée | — | RG19 (postcondition) |
| `FAIL → [*]` | exécution terminée en échec | — | RG20 (postcondition) |

Correspondance états ↔ `classes.md` : `Run.status` (attribut `String status`), `Run.start_date`, `Run.end_date`,
`Run.matched_name_count`, `Run.matched_fqdn_count`, `Run.matched_ip_count`, `Run.no_match_count`, `Run.error_message`.

### Non représenté

- Les 7 étapes du pipeline §8.2 (initiation, collecte virt, collecte IPAM, upsert, consolidation, anomalies IPAM,
  finalisation) se déroulent **à l'intérieur** de l'état `RUNNING` : ce sont des actions, pas des statuts de `run`.
- Aucun état `PENDING`, `RETRY`, `CANCELLED` ou `PAUSED` : `regles.md` et le dictionnaire n'en définissent pas.
- Compteurs et `error_message` : attributs d'état finaux, pas des états (ils sont portés par les actions de transition).
- Comparaison/exports de runs (RG21→RG24) et cycle de vie de `ConsolidatedAsset` / `Anomaly` : hors périmètre demandé.
