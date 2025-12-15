#!/usr/bin/env python3
import getpass
import os
import sys

from ldap3 import ALL, SIMPLE, SUBTREE, SYNC, Connection, Server
from ldap3.core.exceptions import (
    LDAPException,
    LDAPInvalidCredentialsResult,
    LDAPSocketOpenError,
    LDAPSocketSendError,
)

from . import mydb_config


def parseEntry(entry):
    """extact and return value of first CN in entry (entry is a collection of attributes)
    Example: manager: CN=Last\\, First,OU=Comp,OU=USER ACCOUNTS,OU=Big Sciences,DC=domain,DC=org
    return <Last First>
    """
    if len(entry) < 2:
        return "NA"
    entry = entry.replace(r"\,", "|")
    attrs = entry.split(",")
    for attr in attrs:
        attrKey, value = attr.split("=")
        if attrKey == "CN" or attrKey == "cn":
            if "|" in value:
                last, first = value.split("|")
                return "{} {}".format(first.lstrip(), last)
            else:
                return value
    return "NA"


def is_valid(username: str, password: str):
    """'Simple' user validate via AD. If auth succeedes use LDAP connection
    to get user information
    return <status>, <info>
    <info> is dict with keys 'displayName', 'mail', 'manager'
    """
    ADServer = mydb_config.ADServer
    ADdomain = mydb_config.ADDomain
    ADSearchBase = mydb_config.ADSearchBase
    server = Server(ADServer, port=636, use_ssl=True, get_info=ALL)
    user_dn = f"{username}@{ADdomain}"
    info = {}

    try:
        ldap_conn = Connection(
            server,
            authentication=SIMPLE,
            user=user_dn,
            password=password,
            lazy=False,
            client_strategy=SYNC,
            raise_exceptions=True,
        )
    except LDAPException as err:
        print(f"LDAP connection error: {err}", file=sys.stderr)
        return "noAuth", info
    try:
        ldap_conn.bind()
    except LDAPException as e:
        print(f"ldap3 bind error: {e}", file=sys.stderr)
    #   ldap_conn.open()

    ldapfilter = "(uid={})".format(username)
    Attrs = ["displayName", "uid", "mail", "manager", "department"]
    try:
        sync = ldap_conn.search(
            search_base=ADSearchBase,
            search_filter=ldapfilter,
            search_scope=SUBTREE,
            attributes=Attrs,
        )
    except (LDAPSocketOpenError, LDAPSocketSendError) as e:
        print(f"LDAP search error: {e}", file=sys.stderr)
        return "Error", info
    if not sync or ldap_conn.result["result"] != 0:
        print(f"LDAP Search result: {ldap_conn.result}")
        return ("LDAP Search Error", info)

    """ print response from ldap3 search """
    if len(ldap_conn.entries) == 0:
        print(f"AD_auth: no ldap search results: {username}")
        ldap_conn.unbind()
        return "LDAP Search Failed", info
    for obj in ldap_conn.response:
        if "attributes" not in obj:
            continue
        for k, v in obj["attributes"].items():
            if k == "uid":
                k = "username"
            if k == "displayName":
                displayname = v
                if ", " in v:
                    (last, first) = v.split(", ", 1)
                    v = "{} {}".format(first, last)
            if k == "manager":
                v = parseEntry(v)
            if type(v) is list:
                if len(v) > 0:
                    info[k] = v[0]
                else:
                    info[k] = "None"
            else:
                info[k] = v
    return "Good", info


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("AD_auth testing, supply username as argument")
    password = getpass.getpass(prompt="Password: ", stream=None)
    status, info = is_valid(sys.argv[1], password)
    print(f"status: {status}")
    print(f"info: {info}")
