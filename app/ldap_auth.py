"""Optional TLS-protected directory authentication; no directory credentials are stored."""

import json

from ldap3 import Connection, Server, Tls
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPException,
    LDAPInvalidCredentialsResult,
)
from ldap3.utils.conv import escape_filter_chars


class DirectoryUnavailable(Exception):
    pass


def configure_ldap(app, configured):
    server = configured("LDAP_SERVER", "")
    origin = configured("LDAP_ORIGIN", "LDAP")
    base = configured("LDAP_BASE_DN", "")
    search_filter = configured("LDAP_USER_FILTER", "(uid={username})")
    default_role = configured("LDAP_DEFAULT_ROLE", "lecteur")
    bind_dn = configured("LDAP_BIND_DN", "")
    bind_password = configured("LDAP_BIND_PASSWORD", "")
    try:
        groups = json.loads(configured("LDAP_GROUP_ROLES", "{}"))
    except (TypeError, ValueError) as exc:
        raise ValueError("LDAP_GROUP_ROLES") from exc
    roles = ("admin", "opérateur", "lecteur")
    if (origin not in ("LDAP", "AD") or default_role not in roles or
            not isinstance(groups, dict) or any(not isinstance(key, str) or not key or role not in roles
                                                for key, role in groups.items()) or
            bool(bind_dn) != bool(bind_password)):
        raise ValueError("Invalid LDAP configuration")
    if server and (not isinstance(server, str) or not server.startswith(("ldap://", "ldaps://")) or
                   not base or not isinstance(base, str) or
                   not isinstance(search_filter, str) or search_filter.count("{username}") != 1):
        raise ValueError("Invalid LDAP configuration")
    app.config.update(LDAP_SERVER=server, LDAP_ORIGIN=origin, LDAP_BASE_DN=base,
                      LDAP_USER_FILTER=search_filter, LDAP_DEFAULT_ROLE=default_role,
                      LDAP_GROUP_ROLES=groups, LDAP_BIND_DN=bind_dn, LDAP_BIND_PASSWORD=bind_password)


def authenticate_directory(app, username, password):
    """Return origin and role on a unique successful user bind, otherwise None."""
    url = app.config["LDAP_SERVER"]
    if not url or not username or not password or username != username.strip():
        return None
    search = None
    user = None
    try:
        server = Server(url, use_ssl=url.startswith("ldaps://"), tls=Tls(validate=2),
                        connect_timeout=5, get_info=None)
        search = Connection(server, user=app.config["LDAP_BIND_DN"] or None,
                            password=app.config["LDAP_BIND_PASSWORD"] or None,
                            receive_timeout=5, raise_exceptions=True)
        if not server.ssl and not search.start_tls():
            raise DirectoryUnavailable("Directory TLS unavailable")
        if not search.bind():
            raise DirectoryUnavailable("Directory search bind unavailable")
        query = app.config["LDAP_USER_FILTER"].replace("{username}", escape_filter_chars(username))
        if not search.search(app.config["LDAP_BASE_DN"], query, attributes=["memberOf", "userAccountControl", "nsAccountLock"], size_limit=2):
            return None
        if len(search.entries) != 1:
            return None
        entry = search.entries[0]
        attributes = entry.entry_attributes_as_dict
        if (int((attributes.get("userAccountControl") or [0])[0]) & 2 or
                str((attributes.get("nsAccountLock") or ["FALSE"])[0]).lower() == "true"):
            return None
        user = Connection(server, user=entry.entry_dn, password=password,
                          receive_timeout=5, raise_exceptions=True)
        if not server.ssl and not user.start_tls():
            raise DirectoryUnavailable("Directory TLS unavailable")
        try:
            if not user.bind():
                return None
        except (LDAPBindError, LDAPInvalidCredentialsResult):
            return None
        groups = {str(value).casefold() for value in attributes.get("memberOf", [])}
        role = app.config["LDAP_DEFAULT_ROLE"]
        for group, mapped_role in app.config["LDAP_GROUP_ROLES"].items():
            if group.casefold() in groups and ("lecteur", "opérateur", "admin").index(mapped_role) > (
                    "lecteur", "opérateur", "admin").index(role):
                role = mapped_role
        return app.config["LDAP_ORIGIN"], role
    except LDAPException as exc:
        raise DirectoryUnavailable("Directory request failed") from exc
    except (OSError, TimeoutError) as exc:
        raise DirectoryUnavailable("Directory connection failed") from exc
    finally:
        if user is not None and user.bound:
            user.unbind()
        if search is not None and search.bound:
            search.unbind()
