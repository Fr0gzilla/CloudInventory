# app/static/vendor — versions, provenance, licences

Six fichiers servis en local (aucune dépendance CDN runtime ; CSP `script-src 'self'`).
Versions imposées par le cahier des charges §5.1. Chemins relatifs à `app/static/vendor/`.

| # | Fichier | Produit | Version |
|---|---------|---------|---------|
| 1 | `bootstrap.min.css` | Bootstrap (CSS) | 5.3.3 |
| 2 | `bootstrap.bundle.min.js` | Bootstrap (JS bundle, popper inclus) | 5.3.3 |
| 3 | `bootstrap-icons.min.css` | Bootstrap Icons (CSS) | 1.11.3 |
| 4 | `fonts/bootstrap-icons.woff2` | Bootstrap Icons (fonte) | 1.11.3 |
| 5 | `fonts/bootstrap-icons.woff` | Bootstrap Icons (fonte) | 1.11.3 |
| 6 | `chart.umd.min.js` | Chart.js (UMD) | 4.4.7 |

## Provenance (npm officiel, sans CDN)

| Produit | Version | Package npm | Fichiers source dans le tarball |
|---------|---------|-------------|--------------------------------|
| Bootstrap | 5.3.3 | `bootstrap@5.3.3` (registry.npmjs.org) | `dist/css/bootstrap.min.css`, `dist/js/bootstrap.bundle.min.js` |
| Bootstrap Icons | 1.11.3 | `bootstrap-icons@1.11.3` | `font/bootstrap-icons.min.css`, `font/fonts/bootstrap-icons.woff2`, `font/fonts/bootstrap-icons.woff` |
| Chart.js | 4.4.7 | `chart.js@4.4.7` | `dist/chart.umd.js` → livré sous le nom `chart.umd.min.js` |

- Chaque tarball a été vérifié par son `dist.integrity` npm (sha512) ; les six fichiers livrés sont
  octet à octet identiques à ceux des tarballs (contrôle `cmp`, 6/6).
- Aucune copie depuis `reference/` (ce dossier ne contient ni `.css`, ni `.js`, ni fonte).

## Licences

Tous les fichiers sont sous licence **MIT** ; la notice est conservée dans la bannière de tête de chaque
fichier texte, les binaires de fonte gardent leurs tables de noms (aucun fichier `LICENSE` séparé :
plafond des six fichiers).

| Produit | Licence | Titulaire indiqué dans la bannière |
|---------|---------|-------------------------------------|
| Bootstrap 5.3.3 (css + js) | MIT — github.com/twbs/bootstrap/blob/main/LICENSE | Copyright 2011-2024 The Bootstrap Authors |
| Bootstrap Icons 1.11.3 (css + fontes) | MIT — github.com/twbs/icons/blob/main/LICENSE | Copyright 2019-2024 The Bootstrap Authors |
| Chart.js 4.4.7 | MIT (Released under the MIT License) | (c) 2024 Chart.js Contributors ; inclut `@kurkle/color v0.3.2` |

## Écarts et notes

- **Chart.js** : le paquet npm 4.4.7 ne publie que `dist/chart.umd.js` (déjà minifié par le build
  officiel) ; il est livré renommé `chart.umd.min.js`, nom attendu par les pages. Le nom `.min.js`
  servi par jsDelivr correspond à une minification CDN, **inaccessible ici (HTTP 403)** — comme
  `unpkg.com` — d'où le téléchargement via npm.
- **Deux fontes, pas trois** : `bootstrap-icons@1.11.3` ne livre pas de `.ttf` ; le `@font-face` du CSS
  officiel ne référence que `bootstrap-icons.woff2` et `bootstrap-icons.woff`, tous deux présents.
- Seules des références relatives subsistent (`url("fonts/…")` du CSS Icons résolus à côté de lui,
  `sourceMappingURL=*.map` consultables uniquement hors production) : aucun `http(s)://` ni `@import`
  distant dans les six fichiers.

## Contrôles déjà exécutés (constat de livraison)

- sha512 des tarballs npm comparé à `dist.integrity` : OK (3 paquets).
- `cmp` des six fichiers contre les tarballs : 6/6 identiques.
- `node --check` sur `bootstrap.bundle.min.js` et `chart.umd.min.js` : OK.
- Liens locaux des fontes depuis `bootstrap-icons.min.css` : résolus vers `fonts/`.
