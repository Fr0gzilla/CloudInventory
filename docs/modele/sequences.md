# Séquences — Pipeline et authentification

## Diagramme — pipeline d'un run

```mermaid
sequenceDiagram
    actor Administrateur
    participant Run
    actor Virtualisation as Source de virtualisation
    actor IPAM as Source IPAM
    participant Asset
    participant IpamRecord
    participant ConsolidatedAsset
    participant Anomaly
    participant AnomalyException
    Administrateur-)Run: déclencher inventaire sans attendre la fin
    Note over Run: Une transaction pour les sept étapes
    Run->>Run: 1 start() : RUNNING, started_at
    Note over Run: Progression consultable par étape, propre à ce run
    Run->>+Virtualisation: 2 collectAssets() : VM et CT
    Virtualisation-->>-Run: observations VM et CT, origine vSphere qualifiée
    Run->>+IPAM: 3 collectIpamRecords()
    IPAM-->>-Run: observations IPAM, origine EfficientIP qualifiée
    Run->>+Asset: 4 rapprocher par origine virtuelle et vm_id dans ce run
    Asset-->>-Run: observations Asset conservées
    Run->>+IpamRecord: 4 rapprocher par IP et nom DNS dans ce run
    IpamRecord-->>-Run: observations IPAM conservées
    Run->>+ConsolidatedAsset: 5 matchByPriority() : nom, FQDN réduit, IP, aucun
    ConsolidatedAsset-->>-Run: résultat et catégorie de correspondance
    Run->>+Asset: 5 deriveRole() : convention, tag, indéterminé
    Asset-->>-Run: rôle
    Run->>+Anomaly: 5 constater les anomalies de correspondance
    Anomaly-->>-Run: anomalies
    opt vm_id identique et vm_name changé depuis le run précédent
        Run->>+Anomaly: 5 detectRename(otherRun) : constater RENAMED
        Anomaly-->>-Run: anomalie RENAMED
    end
    opt exception applicable par code, cible et expiration
        Run->>+AnomalyException: 5 appliesTo(anomaly)
        AnomalyException-->>-Run: écarter le constat correspondant
    end
    Run->>+IpamRecord: 6 detectDuplicateDns() et detectDuplicateIp()
    IpamRecord-->>-Run: doublons DNS et IP
    Run->>+Anomaly: 6 constater DUPLICATE_DNS et DUPLICATE_IP
    Anomaly-->>-Run: anomalies de doublons
    alt toutes les étapes réussissent
        Run->>Run: 7 finish() : SUCCESS, finished_at, quatre compteurs
        Run->>Run: progression finale consultable
    else exception pendant le pipeline
        Run->>Run: rollback des données collectées puis fail() : FAIL, error_message
        Run->>Run: garder l'étape atteinte sans observations annulées
    end
    Administrateur->>Run: consulter progression et statut du run
    Run-->>Administrateur: étape atteinte et statut
```

## Légende — pipeline

- Flèche pleine : appel ou résultat ; flèche asynchrone : lancement non bloquant ; `alt` : réussite ou exception ; `opt` : traitement conditionnel. `Run`, `Asset`, `IpamRecord`, `ConsolidatedAsset`, `Anomaly` et `AnomalyException` sont les classes du modèle ; l'administrateur et les deux sources sont les acteurs des règles.
- Les étapes 1–7 sont ordonnées ; l'étape 4 représente la conservation/rapprochement intra-run, sans imposer de primitive SQL d'upsert absente du schéma. La branche d'exception peut survenir à chacune des étapes, et non uniquement après la détection des doublons.
- `Asset.virtual_source` et `IpamRecord.ipam_source` conservent la provenance des valeurs reçues dans chaque run, même pour un enregistrement IPAM sans VM ; aucun `vm_id` d'une autre source n'est assimilé sur sa seule valeur. Les constats et observations des anciens runs ne sont pas écrasés.

## Traçabilité — pipeline

| Échanges | Règles et cahier des charges |
| --- | --- |
| Déclenchement et étape 1 | RG19 ; §8.2, étape 1 |
| Étapes 2–3 : deux collectes | RG02–RG03, RG25 ; §8.2, étapes 2–3 |
| Étape 4 : observations propres au run | RG07, RG21, RG24, RG26 ; §6.3, §8.2, étape 4 |
| Étape 5 : priorité, rôle, anomalies | RG09–RG16, RG26 ; §7.1–§7.3, §8.2, étape 5 |
| Étape 6 : doublons | RG08, RG17–RG18, RG27 ; §7.4, §8.2, étape 6 |
| Étape 7 et exception | RG19–RG20, RG28 ; §8.2, étape 7 et rollback |
| Renommage et exception applicable | RG39–RG40, RG44–RG45 ; §18 EV12, EV15 |
| Lancement non bloquant et consultation par étape | RG46, RG48 ; §20 EV30 |
| Collecte qualifiée vSphere et EfficientIP, identité distincte par origine | RG49–RG51, RG58 ; §17 EV01–EV02, §23 EV59 |

## Non représenté — pipeline

- Les multiplicités d'observations sont implicites ; Q1–Q6 ne fixent ni conversions source, ni arbitrage de plusieurs correspondances, ni granularité définitive des doublons. Le modèle n'introduit aucun statut ou anomalie supplémentaire.
- Le mécanisme de persistance de FAIL après annulation de la transaction unique n'est pas prescrit ; la trace montre l'obligation métier sans inventer sa mise en œuvre.
- Les sept étapes actualisent la progression (RG46) sans fixer les valeurs finales après rollback (Q13). Le planificateur externe peut déclencher un nouveau run selon `RunSchedule` (RG47–RG48), mais sa cadence et son fuseau restent hors de ce scénario manuel (Q15). Le suivi ouverte → acquittée → résolue est détaillé dans `etats.md`, pas dans le pipeline.
- L'autorisation d'un lancement et sa trace d'audit sont illustrées dans le scénario d'accès ci-dessous ; elles ne modifient ni les sept étapes ni le cycle du run.

## Diagramme — authentification web et API

```mermaid
sequenceDiagram
    actor Administrateur
    participant AuthWeb as Authentification web
    participant AuthAPI as Authentification API
    opt connexion web
        Administrateur->>AuthWeb: soumettre le formulaire de connexion
        AuthWeb->>AuthWeb: limiter les essais et vérifier le compte unique haché
        alt identifiants valides
            AuthWeb-->>Administrateur: ouvrir une session signée
            Administrateur->>AuthWeb: demander une route web protégée
            AuthWeb->>AuthWeb: vérifier la session et next interne
            AuthWeb-->>Administrateur: accès à la route
        else session absente ou identifiants invalides
            AuthWeb-->>Administrateur: rediriger vers /login pour route protégée
        end
    end
    opt connexion API
        Administrateur->>AuthAPI: POST /api/login avec username et password
        AuthAPI->>AuthAPI: limiter les essais et vérifier les identifiants
        alt identifiants valides
            AuthAPI-->>Administrateur: access_token
            Administrateur->>AuthAPI: appeler une route API avec Bearer token
            AuthAPI->>AuthAPI: vérifier le jeton
            AuthAPI-->>Administrateur: réponse de la route protégée
        else jeton absent ou invalide
            AuthAPI-->>Administrateur: refus 401 sur route protégée
        end
    end
```

## Légende — authentification

- `opt` distingue les deux chemins possibles, sans les rendre simultanément obligatoires ; `alt` expose l'accès autorisé ou le refus. `Administrateur` est l'acteur défini dans les règles ; `Authentification web` et `Authentification API` sont des composants d'interface, non des entités persistées du diagramme de classes.

## Traçabilité — authentification

| Échanges | Règles et cahier des charges |
| --- | --- |
| Connexion et session web signée, compte unique haché | RG29 ; §10.1 |
| Route web protégée et redirection /login | RG30 ; §10.1 |
| Limitation des essais et next interne | RG33–RG34 ; §10, rubrique « Sécurité » |
| POST /api/login et access_token | RG31 ; §9.2 |
| Bearer valide ou refus 401 | RG32 ; §9.2, §9.5, §10.2 |

## Non représenté — authentification

- La gestion des erreurs de formulaire et la durée de vie des sessions et jetons ne sont pas définies ici ; Q7 laisse ouvert le mécanisme de stockage de la session et du compte, sans ajouter d'entité au modèle.
- Les autres protections transverses du §10 ne constituent pas des étapes supplémentaires de connexion dans ces scénarios.

## Diagramme — accès authentifié et audit v2.3

```mermaid
sequenceDiagram
    actor Utilisateur as Administrateur, opérateur ou lecteur
    participant Account
    participant AuthOrigin
    actor Annuaire as Annuaire LDAP ou Active Directory
    participant Role
    participant RolePermission
    participant Permission
    participant Run
    participant Audit
    Utilisateur->>+Account: authenticate(identifiants, origine)
    Account->>AuthOrigin: identifier local, LDAP ou AD
    alt origine locale
        Account->>Account: vérifier local_password_hash sans conserver le mot de passe en clair
    else origine LDAP ou AD
        Account->>+Annuaire: vérifier les identifiants auprès de l'annuaire
        Annuaire-->>-Account: résultat de l'authentification
        Note over Account,AuthOrigin: Aucun mot de passe annuaire conservé
    end
    Account-->>-Utilisateur: authentification acceptée ou refusée
    opt compte authentifié demandant le lancement d'un run
        Utilisateur->>+Account: authorize(permission de lancement)
        Account->>+Role: allows(permission)
        Role->>RolePermission: chercher une attribution explicite au rôle
        RolePermission->>Permission: vérifier le code de permission attribué
        Permission-->>RolePermission: attribution existante ou absente
        RolePermission-->>Role: permission attribuée ou non
        Role-->>-Account: autoriser uniquement si attribuée
        alt permission explicitement attribuée
            Account-->>Utilisateur: action autorisée
            Utilisateur-)Run: déclencher un nouveau run
            Account->>+Audit: recordAction(auteur, lancement, date, cible du run)
            Audit-->>-Account: trace durable
        else permission absente
            Account-->>Utilisateur: action refusée
        end
    end
```

## Légende — accès et audit

- `alt` distingue les origines locale et annuaire, puis autorisation ou refus ; `opt` limite la demande d'action aux comptes authentifiés. La flèche asynchrone lance le run sans attendre son achèvement. `Account`, `AuthOrigin`, `Role`, `RolePermission`, `Permission`, `Run` et `Audit` sont les classes du modèle ; utilisateur et annuaire sont les acteurs des règles.
- `Account.local_password_hash` n'existe que pour l'origine locale ; l'annuaire authentifie sans empreinte ni mot de passe applicatif. Une permission n'est accordée que par l'association explicite `RolePermission`, sans matrice présumée. `Audit.audit_actor_identity`, `audit_action`, `audit_date` et `audit_target` restent durables après purge de runs.

## Traçabilité — accès et audit

| Échanges | Règles et cahier des charges |
| --- | --- |
| Compte distinct et origine locale ou annuaire | RG53–RG56 ; §22 EV48–EV49 |
| Autorisation par rôle et permission attribuée | RG52 ; §22 EV48 |
| Trace du lancement, auteur, action, date et cible durables | RG57–RG58 ; §22 EV52, §23 EV59 |

## Non représenté — accès et audit

- La matrice effective des permissions, l'association entre identités d'origines différentes et la rétention des audits restent ouvertes (Q17–Q19). Acquittement, purge et modification suivent la même obligation de trace après autorisation ; aucun droit n'est inventé pour ces opérations. La purge des runs ne purge pas les audits.
- La représentation d'une authentification acceptée ou refusée est condensée en réponse ; seuls les comptes effectivement authentifiés peuvent demander l'autorisation. La forme des échanges réseau avec l'annuaire n'est pas prescrite.
