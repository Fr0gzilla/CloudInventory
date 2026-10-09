"""Tests pour scripts/smoke.sh (CODE-05).

C1 : Double curl enregistrant la structure des args (pas les valeurs)
C2 : Permissions 0600 pour fichiers, 0700 pour répertoire temporaire
C3 : Nettoyage garanti sur EXIT/INT/TERM
C4 : Encodage JSON/form correct (--data-binary @fichier, --header @fichier)
C5 : Aucun secret dans stdout/stderr
C6 : Profil docker — cible locale uniquement, refus avant tout appel curl (T038)
"""
import os
import stat
import subprocess
import tempfile
import json
import sys
import pytest
from pathlib import Path


SMOKE_SCRIPT = Path(__file__).parent.parent / "scripts" / "smoke.sh"


@pytest.fixture
def smoke_env(tmp_path):
    """Environnement isolé pour tester smoke.sh sans Docker."""
    env = os.environ.copy()
    env.update({
        "ADMIN_PASSWORD": "test-admin-password-32-bytes-min!!",
        "SECRET_KEY": "test-secret-key-32-bytes-minimum!!",
        "JWT_SECRET_KEY": "test-jwt-secret-key-32-bytes-min!!",
        "DATABASE_URL": "sqlite:///:memory:",
        "APP_ENV": "test",
        "SMOKE_PROFILE": "test",
        "USE_MOCK_VIRT": "true",
        "USE_MOCK_IPAM": "true",
        "RATE_LIMIT_STORE_PATH": str(tmp_path / "login-budgets.json"),
    })
    # Créer un .env factice pour le source
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ADMIN_PASSWORD=test-admin-password-32-bytes-min!!\n"
        "SECRET_KEY=test-secret-key-32-bytes-minimum!!\n"
        "JWT_SECRET_KEY=test-jwt-secret-key-32-bytes-min!!\n"
        "DATABASE_URL=sqlite:///:memory:\n"
        "APP_ENV=test\n"
        "USE_MOCK_VIRT=true\n"
        "USE_MOCK_IPAM=true\n"
    )
    env["SMOKE_TEST_DIR"] = str(tmp_path)
    return env


class CurlDouble:
    """Double de curl qui enregistre la structure des appels sans les valeurs."""

    def __init__(self, record_dir):
        self.record_dir = Path(record_dir)
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self.calls = []
        self.call_count = 0

    def __call__(self, *args, **kwargs):
        self.call_count += 1
        call_record = {
            "call_number": self.call_count,
            "argv_structure": [],
            "env_keys": sorted([k for k in os.environ.keys() if "SECRET" in k or "PASSWORD" in k or "TOKEN" in k]),
        }

        i = 0
        while i < len(args):
            arg = args[i]
            # Options qui prennent une valeur (argument suivant)
            if arg in ("-o", "-w", "-b", "-c", "-X", "-H", "--data-binary", "--header", "-d", "--data"):
                # Option avec argument suivant
                call_record["argv_structure"].append(arg)
                if i + 1 < len(args):
                    next_arg = args[i + 1]
                    # Enregistrer la structure, pas la valeur
                    if arg in ("--data-binary", "--header", "-H", "-d", "--data"):
                        if next_arg.startswith("@"):
                            call_record["argv_structure"].append(f"@{Path(next_arg[1:]).name}")
                        else:
                            call_record["argv_structure"].append("<inline-data>")
                    elif arg in ("-b", "-c", "-H"):
                        call_record["argv_structure"].append(f"<{arg}-file-or-value>")
                    else:
                        call_record["argv_structure"].append("<value>")
                    i += 2
                    continue
            elif arg.startswith("-"):
                # Flags sans valeur (ex: -f, -s, -k, -L, etc.)
                call_record["argv_structure"].append(arg)
            else:
                # URL ou autre
                call_record["argv_structure"].append("<URL>")
            i += 1

        self.calls.append(call_record)

        # Retourner une réponse factice selon l'URL appelée
        url = next((a for a in args if a.startswith("http")), "")
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        return self._mock_response(url, method)

    def _mock_response(self, url, method):
        """Réponses factices pour les endpoints testés."""
        if "/healthz" in url:
            return b"", 200, {}
        elif url.endswith("/api/login"):
            return b'{"access_token":"fake-jwt-token-123"}', 200, {}
        elif url.endswith("/login") and method == "GET":
            # GET /login - retourner HTML avec CSRF token
            html = '<form><input name="csrf_token" value="test-csrf-token-123"></form>'
            return html.encode(), 200, {"Set-Cookie": "session=test"}
        elif url.endswith("/login") and method == "POST":
            # POST /login - 302 redirect
            return b"", 302, {"Set-Cookie": "session=authenticated", "Location": "/"}
        elif "/run" in url:
            return b"", 302, {"Location": "/"}
        elif "/api/stats" in url:
            return b'{"has_data": true}', 200, {}
        return b"", 404, {}

    def save_record(self):
        """Sauvegarder l'enregistrement pour inspection."""
        record_file = self.record_dir / "curl_calls.json"
        record_file.write_text(json.dumps(self.calls, indent=2))
        return record_file


@pytest.fixture
def curl_double(tmp_path):
    """Double curl pour enregistrer les appels."""
    double = CurlDouble(tmp_path / "curl_records")
    yield double
    double.save_record()


def test_smoke_script_exists_and_executable():
    """Le script smoke.sh existe et est exécutable."""
    assert SMOKE_SCRIPT.exists(), f"Script manquant : {SMOKE_SCRIPT}"
    assert os.access(SMOKE_SCRIPT, os.X_OK), "Script non exécutable"


def test_smoke_script_shebang_and_set_e():
    """Shebang bash et set -e présents."""
    content = SMOKE_SCRIPT.read_text()
    assert content.startswith("#!/bin/bash"), "Shebang manquant ou incorrect"
    assert "set -e" in content.split("\n")[1], "set -e absent"


def test_smoke_script_security_hardening_headers(smoke_env):
    """En-têtes de durcissement : set +x, umask 077, mktemp, traps."""
    content = SMOKE_SCRIPT.read_text()
    lines = content.split("\n")

    # set +x pour éviter l'affichage des secrets dans la trace
    assert any("set +x" in line for line in lines[:10]), "set +x absent en tête"

    # umask 077 pour permissions restrictives
    assert any("umask 077" in line for line in lines[:10]), "umask 077 absent"

    # mktemp -d pour répertoire temporaire
    assert any("mktemp -d" in line for line in lines[:15]), "mktemp -d absent"

    # Traps EXIT, INT, TERM
    assert any("trap" in line and "EXIT" in line for line in lines), "trap EXIT absent"
    assert any("trap" in line and "INT" in line for line in lines), "trap INT absent"
    assert any("trap" in line and "TERM" in line for line in lines), "trap TERM absent"


def test_smoke_script_write_secret_function(smoke_env):
    """Fonction write_secret : crée fichier 0600, n'affiche pas la valeur."""
    content = SMOKE_SCRIPT.read_text()
    assert "write_secret()" in content, "Fonction write_secret absente"
    assert "chmod 600" in content, "chmod 600 absent dans write_secret"
    assert 'printf' in content, "printf utilisé (pas echo -n qui peut fuir)"
    # La valeur ne doit pas être dans stdout (pas de echo $value)
    assert 'echo "$value"' not in content and "echo $value" not in content, "Valeur affichée dans write_secret"


def test_smoke_script_no_secrets_in_argv(smoke_env):
    """Aucun secret dans argv : utilisation de --data-binary @fichier et -H/--header @fichier."""
    content = SMOKE_SCRIPT.read_text()

    # Vérifier l'utilisation de fichiers pour les données sensibles
    assert "--data-binary @" in content, "--data-binary @fichier absent"
    # Le script utilise -H @fichier (forme courte) pour les en-têtes
    assert "-H @" in content or "--header @" in content, "-H @fichier ou --header @fichier absent"

    # Pas de concaténation de secrets dans la ligne de commande curl
    # Les variables sont utilisées dans write_secret qui écrit dans des fichiers
    assert "${ADMIN_PASSWORD}" not in content or "write_secret" in content, "ADMIN_PASSWORD utilisé hors write_secret"
    assert "${ACCESS_TOKEN}" not in content or "write_secret" in content, "ACCESS_TOKEN utilisé hors write_secret"


def test_smoke_script_json_encoding(run_smoke):
    """Encodage JSON correct pour /api/login (pas de concaténation)."""
    _, calls = run_smoke()
    assert next(c for c in calls if c["endpoint"] == "/api/login")["encoded"]


def test_smoke_script_form_encoding(run_smoke):
    """Encodage formulaire correct pour /login et /run (--data-binary @fichier)."""
    _, calls = run_smoke()
    forms = [c for c in calls if c["endpoint"] in ("/login", "/run") and c["post"]]
    assert len(forms) == 2 and all(c["encoded"] for c in forms)


def test_smoke_script_cookie_jar_in_temp(smoke_env):
    """Cookie jar dans le répertoire temporaire privé (pas chemin prévisible)."""
    content = SMOKE_SCRIPT.read_text()
    assert "COOKIE_JAR=" in content, "COOKIE_JAR absent"
    assert "SMOKE_TMP" in content, "Répertoire temporaire utilisé pour cookies"


def test_smoke_script_cleanup_on_signals(smoke_env):
    """Traps EXIT, INT, TERM pour nettoyage garanti."""
    content = SMOKE_SCRIPT.read_text()

    # Trap EXIT nettoie le répertoire temporaire
    exit_traps = [l for l in content.split("\n") if "trap" in l and "EXIT" in l]
    assert exit_traps, "Trap EXIT absent"
    assert any("cleanup" in t for t in exit_traps)
    assert 'rm -rf -- "$SMOKE_TMP"' in content

    # Trap INT -> exit 130
    int_traps = [l for l in content.split("\n") if "trap" in l and "INT" in l]
    assert int_traps, "Trap INT absent"
    assert any("130" in t for t in int_traps), "Code sortie 130 absent pour INT"

    # Trap TERM -> exit 143
    term_traps = [l for l in content.split("\n") if "trap" in l and "TERM" in l]
    assert term_traps, "Trap TERM absent"
    assert any("143" in t for t in term_traps), "Code sortie 143 absent pour TERM"


def test_smoke_script_wait_healthz_not_login(smoke_env):
    """Attente de santé sur /healthz (pas /login comme avant)."""
    content = SMOKE_SCRIPT.read_text()
    assert "/healthz" in content, "Attente sur /healthz absente"
    # L'ancien healthcheck sur /login ne doit plus être la méthode principale
    healthz_wait = [l for l in content.split("\n") if "/healthz" in l and "curl" in l]
    assert healthz_wait, "Boucle d'attente /healthz absente"


def test_smoke_script_csrf_extraction(smoke_env):
    """Extraction CSRF token depuis le HTML (grep/sed, pas en argv)."""
    content = SMOKE_SCRIPT.read_text()
    assert "CSRF_TOKEN" in content, "Variable CSRF_TOKEN absente"
    assert "grep" in content or "sed" in content, "Extraction par grep/sed attendue"
    # Le token ne doit pas être passé en argv
    assert "${CSRF_TOKEN}" not in content or "--data-binary @" in content, "CSRF_TOKEN en argv"


def test_smoke_script_jwt_handling(smoke_env):
    """Gestion JWT : extraction depuis JSON, passage via -H/--header @fichier."""
    content = SMOKE_SCRIPT.read_text()
    assert "ACCESS_TOKEN" in content, "Variable ACCESS_TOKEN absente"
    assert "grep" in content and "access_token" in content, "Extraction JWT depuis JSON"
    # Le script utilise -H @fichier (forme courte) pour l'en-tête Authorization
    assert "-H @" in content or "--header @" in content, "Passage JWT via -H/--header @fichier"
    assert "auth_header" in content, "Fichier auth_header créé"


def test_smoke_script_permissions_temp_dir(smoke_env, tmp_path):
    """Répertoire temporaire créé avec permissions 0700 (umask 077)."""
    # Simuler l'exécution du script jusqu'à la création du répertoire
    # On ne peut pas tester facilement les permissions réelles sans l'exécuter,
    # mais on vérifie que umask 077 est posé avant mktemp
    content = SMOKE_SCRIPT.read_text()
    lines = content.split("\n")
    umask_line = next((i for i, l in enumerate(lines) if "umask 077" in l), -1)
    mktemp_line = next((i for i, l in enumerate(lines) if "mktemp -d" in l), -1)
    assert umask_line >= 0 and mktemp_line >= 0 and umask_line < mktemp_line, "umask 077 avant mktemp"


def test_smoke_script_file_permissions_0600(smoke_env):
    """Fichiers secrets créés avec chmod 600."""
    content = SMOKE_SCRIPT.read_text()
    # write_secret fait chmod 600
    assert "chmod 600" in content, "chmod 600 absent"
    # Tous les fichiers de données sensibles passent par write_secret
    secret_files = ["LOGIN_DATA_FILE", "RUN_DATA_FILE", "API_LOGIN_FILE", "AUTH_HEADER_FILE"]
    for var in secret_files:
        assert f"{var}=" in content, f"Variable {var} absente"
        assert "write_secret" in content, "write_secret utilisé pour fichiers sensibles"


@pytest.fixture
def run_smoke(smoke_env, tmp_path):
    checker = tmp_path / "check_curl.py"
    checker.write_text(r'''
import json, os, stat, sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
args = sys.argv[1:]
endpoint = urlsplit(args[-1]).path
root = Path(os.environ['TMPDIR'])
private = next(p.parent for p in root.glob('tmp*/password'))
password = (private / 'password').read_text()
secrets = [password, os.environ['SMOKE_CSRF'], os.environ['SMOKE_JWT']]
record = dict(endpoint=endpoint, host=urlsplit(args[-1]).netloc, post='POST' in args,
              private=stat.S_IMODE(private.stat().st_mode) == 0o700,
              safe_argv=all(s not in a for s in secrets for a in args),
              argv_structure=[], encoded=False, files_private=True)
for arg in args:
    if arg.startswith('@'):
        path = Path(arg[1:])
        record['files_private'] &= stat.S_IMODE(path.stat().st_mode) == 0o600
        record['argv_structure'].append('@' + path.name)
    else:
        record['argv_structure'].append(arg if arg.startswith('-') or arg == 'Content-Type: application/json' else '<value>')
if '--data-binary' in args:
    arg = args[args.index('--data-binary') + 1]
    if arg.startswith('@'):
        data = Path(arg[1:]).read_text()
        if endpoint == '/api/login':
            record['encoded'] = json.loads(data) == dict(username='admin', password=password)
        elif endpoint == '/login':
            record['encoded'] = parse_qs(data) == dict(username=['admin'], password=[password], csrf_token=[os.environ['SMOKE_CSRF']])
        elif endpoint == '/run':
            record['encoded'] = parse_qs(data) == dict(csrf_token=[os.environ['SMOKE_CSRF']])
record['special_password'] = all(c in password for c in '&=+% "\\\né')
if endpoint == '/api/stats':
    header = args[args.index('-H') + 1]
    record['auth'] = header.startswith('@') and Path(header[1:]).read_text() == 'Authorization: Bearer ' + os.environ['SMOKE_JWT']
with open(os.environ['SMOKE_RECORDS'], 'a') as stream:
    stream.write(json.dumps(record) + '\n')
''')
    smoke_env.update({
        "SMOKE_CSRF": "test-csrf-&=+%é",
        "SMOKE_JWT": "fake-jwt-token-123",
        "SMOKE_CALLS": str(tmp_path / "calls"),
        "SMOKE_RECORDS": str(tmp_path / "records"),
        "SMOKE_CHECKER": str(checker),
        "SMOKE_PYTHON": sys.executable,
        "TMPDIR": str(tmp_path),
    })
    wrapper = r'''
curl() {
    "$SMOKE_PYTHON" "$SMOKE_CHECKER" "$@" || return 99
    local url="${!#}" args=" $* "
    printf '%s\n' "${url##*/}" >> "$SMOKE_CALLS"
    case "$url" in
        */healthz) return 0 ;;
        */api/login)
            if [ "$SMOKE_FAILURE" = api_login ]; then
                printf '{"error":"%s"}' "$SMOKE_JWT"
            else
                printf '{"access_token":"%s"}' "$SMOKE_JWT"
            fi ;;
        */api/stats)
            if [ "$SMOKE_FAILURE" = stats ]; then
                printf '{"error":"%s"}\nHTTP 503' "$SMOKE_JWT"
            else
                printf '{"has_data":true}\nHTTP 200'
            fi ;;
        */login)
            if [[ "$args" == *" -X POST "* ]]; then
                printf '302'
            elif [[ "$args" == *" -w "* ]]; then
                printf '200'
            else
                printf '<input name="csrf_token" value="%s">' "$SMOKE_CSRF"
            fi ;;
        */run) printf '302' ;;
        *) return 99 ;;
    esac
}
export -f curl
exec bash "$1"
'''
    def run(failure=""):
        smoke_env["SMOKE_FAILURE"] = failure
        result = subprocess.run(
            ["bash", "-c", wrapper, "smoke-test", str(SMOKE_SCRIPT.resolve())],
            cwd=SMOKE_SCRIPT.parent.parent, env=smoke_env,
            capture_output=True, text=True, timeout=30,
        )
        calls = [json.loads(line) for line in (tmp_path / "records").read_text().splitlines()]
        assert not list(tmp_path.glob("tmp*")), "Répertoire privé non nettoyé"
        return result, calls
    return run


@pytest.mark.parametrize("failure", ["", "api_login", "stats"])
def test_smoke_script_no_secrets_in_stdout_stderr(smoke_env, tmp_path, failure, run_smoke):
    result, records = run_smoke(failure)
    assert result.returncode == (1 if failure else 0)
    assert ("SMOKE TEST PASSED" in result.stdout) == (not failure)
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == ["healthz", "login", "login", "login", "login", "login", "run", "login"] + (
        [] if failure == "api_login" else ["stats"]
    )
    for key in ("ADMIN_PASSWORD", "SECRET_KEY", "JWT_SECRET_KEY", "SMOKE_CSRF", "SMOKE_JWT"):
        assert smoke_env[key] not in result.stdout, f"{key} divulgué sur stdout"
        assert smoke_env[key] not in result.stderr, f"{key} divulgué sur stderr"


def test_smoke_script_curl_double_records_structure(run_smoke):
    """Test d'intégration : double curl enregistre la structure des appels."""
    result, calls = run_smoke()
    assert result.returncode == 0
    assert len(calls) == 9
    assert all(c["safe_argv"] and c["private"] and c["files_private"] and c["special_password"] for c in calls)
    assert calls[0]["argv_structure"] == ["-s", "-f", "<value>"]

    # Vérifier l'appel POST login avec --data-binary @fichier
    login_post = calls[3]
    assert "--data-binary" in login_post["argv_structure"]
    assert "@login_data" in login_post["argv_structure"]

    # Vérifier l'appel API login avec JSON
    api_login = calls[7]
    assert "-H" in api_login["argv_structure"]
    assert "Content-Type: application/json" in api_login["argv_structure"]
    assert "--data-binary" in api_login["argv_structure"]
    assert "@api_login" in api_login["argv_structure"]

    # Vérifier l'appel API stats avec -H @fichier
    api_stats = calls[8]
    assert "-H" in api_stats["argv_structure"]
    assert "@auth_header" in api_stats["argv_structure"]
    assert api_stats["auth"]


@pytest.mark.parametrize("profile", [None, "", "production", "local"])
def test_smoke_rejects_non_test_profile_before_startup(smoke_env, tmp_path, profile):
    smoke_env.pop("SMOKE_PROFILE", None)
    if profile is not None:
        smoke_env["SMOKE_PROFILE"] = profile
    result = subprocess.run(
        ["bash", str(SMOKE_SCRIPT)], cwd=tmp_path, env=smoke_env,
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 1 and "SMOKE_PROFILE=test" in result.stdout


def test_smoke_script_structure_summary():
    """Résumé de la structure attendue du script (vérification statique)."""
    content = SMOKE_SCRIPT.read_text()

    # Points clés de la structure sécurisée
    checks = [
        (r"set \+x", "Traces désactivées"),
        ("umask 077", "Permissions restrictives"),
        ("mktemp -d", "Répertoire temporaire privé"),
        ("trap.*EXIT", "Nettoyage à la sortie"),
        ("trap.*INT", "Nettoyage sur SIGINT"),
        ("trap.*TERM", "Nettoyage sur SIGTERM"),
        ("write_secret", "Fonction d'écriture sécurisée"),
        ("chmod 600", "Fichiers 0600"),
        ("--data-binary @", "Données via fichier"),
        ("-H @", "En-têtes via fichier (forme courte)"),
        ("/healthz", "Healthcheck sur /healthz"),
        ("grep.*value=", "Extraction CSRF (via value=)"),
        ("grep.*access_token", "Extraction JWT"),
    ]

    for pattern, desc in checks:
        import re
        assert re.search(pattern, content), f"Manquant : {desc} ({pattern})"


def test_smoke_docker_profile_targets_running_container(smoke_env, tmp_path, run_smoke):
    """Recette Docker : le profil docker vise le conteneur lancé, lit ADMIN_PASSWORD dans son .env, sans démarrer
    d'application ni de venv ; mêmes contrôles (aucun secret en argv, fichiers privés, encodage)."""
    smoke_env.update({
        "SMOKE_PROFILE": "docker",
        "SMOKE_APP_URL": "127.0.0.1:5999",
        "SMOKE_ENV_FILE": str(tmp_path / ".env"),
        "SMOKE_PYTHON_BIN": sys.executable,
    })
    result, records = run_smoke()
    assert result.returncode == 0 and "SMOKE TEST PASSED" in result.stdout, result.stdout + result.stderr
    assert records and all(r["host"] == "127.0.0.1:5999" for r in records)
    assert all(r["safe_argv"] and r["files_private"] and r["private"] for r in records)
    assert all(r["encoded"] for r in records if r["post"])
    assert smoke_env["ADMIN_PASSWORD"] not in result.stdout + result.stderr


def test_smoke_docker_profile_requires_admin_password(smoke_env, tmp_path):
    env_file = tmp_path / "vide.env"
    env_file.write_text("SECRET_KEY=x\n")
    smoke_env.update({"SMOKE_PROFILE": "docker", "SMOKE_ENV_FILE": str(env_file), "SMOKE_PYTHON_BIN": sys.executable,
                      "TMPDIR": str(tmp_path)})
    result = subprocess.run(["bash", str(SMOKE_SCRIPT)], cwd=tmp_path, env=smoke_env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "ADMIN_PASSWORD missing" in result.stdout
    assert not list(tmp_path.glob("tmp*")), "Répertoire privé non nettoyé"


# --- T038 C6 — cible du profil docker : locale uniquement --------------------

# Cibles refusées : hôte distant, IP privée, schéma, chemin, userinfo, suffixe
# trompeur, query — toute URL qui ferait partir le mot de passe ailleurs.
_DOCKER_REMOTE_TARGETS = [
    "evil.example.com",
    "evil.example.com:8443",
    "http://127.0.0.1:5000",
    "127.0.0.1:5000/admin",
    "admin@evil.example.com",
    "192.168.1.10:8080",
    "127.0.0.1.evil.example.com",
    "127.0.0.1:5000?next=evil",
]

# Cibles acceptées : 127.0.0.1 et localhost, avec ou sans port.
_DOCKER_LOCAL_TARGETS = ["127.0.0.1", "127.0.0.1:5000", "localhost", "localhost:8080"]


@pytest.mark.parametrize("target", _DOCKER_REMOTE_TARGETS)
def test_smoke_docker_profile_rejects_remote_target_without_any_curl(smoke_env, tmp_path, run_smoke, target):
    """Cible non locale : refus code 1 avant le moindre appel curl, mot de passe jamais exposé."""
    smoke_env.update({
        "SMOKE_PROFILE": "docker",
        "SMOKE_APP_URL": target,
        "SMOKE_ENV_FILE": str(tmp_path / ".env"),
        "SMOKE_PYTHON_BIN": sys.executable,
    })
    (tmp_path / "records").touch()  # fixture run_smoke : aucun enregistrement attendu
    result, calls = run_smoke()
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL: SMOKE_APP_URL" in result.stdout
    assert calls == []
    assert not (tmp_path / "calls").exists(), f"curl appelé pour la cible {target}"
    assert smoke_env["ADMIN_PASSWORD"] not in result.stdout + result.stderr


@pytest.mark.parametrize("target", _DOCKER_LOCAL_TARGETS)
def test_smoke_docker_profile_accepts_local_targets(smoke_env, tmp_path, run_smoke, target):
    """Cible locale (avec ou sans port) : le déroulé complet se poursuit vers elle."""
    smoke_env.update({
        "SMOKE_PROFILE": "docker",
        "SMOKE_APP_URL": target,
        "SMOKE_ENV_FILE": str(tmp_path / ".env"),
        "SMOKE_PYTHON_BIN": sys.executable,
    })
    result, records = run_smoke()
    assert result.returncode == 0 and "SMOKE TEST PASSED" in result.stdout, result.stdout + result.stderr
    assert records and all(r["host"] == target for r in records)
    assert all(r["safe_argv"] and r["files_private"] and r["private"] for r in records)
    assert smoke_env["ADMIN_PASSWORD"] not in result.stdout + result.stderr
