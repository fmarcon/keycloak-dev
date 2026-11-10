#!/usr/bin/env bash
# Seed unico + slapd em foreground. Baseado no cont-init do
# rroemhild/docker-test-openldap (MIT License, Copyright (c) 2016 Rafael Roemhild).
#
# Comportamento:
# - volumes /var/lib/ldap e /etc/ldap/slapd.d persistem entre restarts/down-up;
# - seed (schema, overlays, senha do cn=config, dados Futurama) roda UMA vez,
#   controlado pelo marker /var/lib/ldap/.seeded;
# - LDAP_RESEED=1 apaga banco+config e re-seeda (entryUUIDs mudam! imports do
#   Keycloak ficam stale; apague o realm dev e reinicie o keycloak).
set -eu

MARKER=/var/lib/ldap/.seeded

start_slapd_foreground() {
    mkdir -p /run/slapd
    chown openldap:openldap /run/slapd
    exec /usr/sbin/slapd \
        -h "ldapi:/// ldap://0.0.0.0:10389 ldaps://0.0.0.0:10636" \
        -u openldap -g openldap -d 256
}

stop_tmp_slapd() {
    [ -f /run/slapd/slapd.pid ] && kill -INT "$(cat /run/slapd/slapd.pid)" || true
    for _ in $(seq 1 10); do
        [ ! -f /run/slapd/slapd.pid ] && return 0
        sleep 0.5
    done
    echo "WARN: slapd temporario nao parou; continuando mesmo assim." >&2
}

seed() {
    echo "== Seed inicial do LDAP (${LDAP_DOMAIN}) =="

    echo "Reconfigurando slapd (dpkg-reconfigure)..."
    cat <<EOL | debconf-set-selections
slapd slapd/internal/generated_adminpw password ${LDAP_SECRET}
slapd slapd/internal/adminpw password ${LDAP_SECRET}
slapd slapd/password2 password ${LDAP_SECRET}
slapd slapd/password1 password ${LDAP_SECRET}
slapd slapd/domain string ${LDAP_DOMAIN}
slapd shared/organization string ${LDAP_ORGANISATION}
slapd slapd/backend string MDB
slapd slapd/purge_database boolean true
slapd slapd/move_old_database boolean true
slapd slapd/allow_ldap_v2 boolean false
slapd slapd/no_configuration boolean false
slapd slapd/dump_database select when needed
EOL
    DEBIAN_FRONTEND=noninteractive dpkg-reconfigure slapd

    mkdir -p /var/lib/ldap /etc/ldap/slapd.d /run/slapd
    chown -R openldap:openldap /var/lib/ldap /run/slapd
    chmod 750 /var/lib/ldap

    # slapd temporario so no socket ldapi (permite alterar cn=config via EXTERNAL)
    /usr/sbin/slapd -h "ldapi:///" -u openldap -g openldap
    stop_slapd() { stop_tmp_slapd; }
    trap stop_slapd EXIT

    echo "Aplicando schema e overlays..."
    ldapmodify -Y EXTERNAL -H ldapi:/// -Q \
        -f /opt/openldap/bootstrap/config/msad.ldif
    ldapmodify -Y EXTERNAL -H ldapi:/// -Q \
        -f /opt/openldap/bootstrap/config/memberof.ldif
    ldapmodify -Y EXTERNAL -H ldapi:/// -Q \
        -f /opt/openldap/bootstrap/config/logging.ldif

    echo "Definindo senha do cn=config..."
    local adminpw
    adminpw=$(slappasswd -h '{SSHA}' -s "${LDAP_SECRET}")
    cat > /tmp/configadminpw.ldif <<EOF
dn: olcDatabase={0}config,cn=config
changetype: modify
replace: olcRootPW
olcRootPW: ${adminpw}
EOF
    ldapmodify -Y EXTERNAL -H ldapi:/// -Q -f /tmp/configadminpw.ldif
    rm -f /tmp/configadminpw.ldif

    echo "Carregando dados iniciais..."
    local ldif
    for ldif in $(find /opt/openldap/bootstrap/data -maxdepth 1 -name '*_*.ldif' -type f | sort); do
        echo "  $(basename "$ldif")"
        ldapadd -x -H ldapi:/// -D "${LDAP_BINDDN}" -w "${LDAP_SECRET}" -f "$ldif"
    done

    touch "$MARKER"
    chown openldap:openldap "$MARKER"

    stop_slapd
    trap - EXIT
    echo "== Seed concluido =="

    sleep 1
}

if [ "${LDAP_RESEED}" = "1" ] || [ "${LDAP_RESEED}" = "true" ]; then
    echo "== LDAP_RESEED: apagando banco e configuracao =="
    rm -rf /var/lib/ldap/* /var/lib/ldap/.seeded /etc/ldap/slapd.d/*
fi

if [ -f "$MARKER" ]; then
    echo "Banco ja semeado (marker presente); iniciando slapd."
    start_slapd_foreground
fi

seed
start_slapd_foreground
