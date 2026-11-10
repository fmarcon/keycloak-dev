FROM debian:bookworm-slim

# Copiado de rroemhild/docker-test-openldap (MIT License, Copyright (c) 2016 Rafael Roemhild):
# https://github.com/rroemhild/docker-test-openldap
# Diferenca: entrypoint proprio que so faz seed na primeira vez (marker em
# /var/lib/ldap), permitindo persistencia real em volumes nomeados.

# Impede que scripts de maintainer iniciem servicos durante apt/dpkg.
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod +x /usr/sbin/policy-rc.d

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        slapd \
        ldap-utils \
    && rm -rf /var/lib/apt/lists/*

# Dominio fixo: os LDIFs de dados usam dc=planetexpress,dc=com hardcoded.
# Trocar LDAP_DOMAIN exige editar tambem bootstrap/data/*.ldif (e o bind DN).
ENV LDAP_DOMAIN=planetexpress.com \
    LDAP_ORGANISATION="Planet Express, Inc." \
    LDAP_BINDDN="cn=admin,dc=planetexpress,dc=com" \
    LDAP_SECRET=GoodNewsEveryone \
    LDAP_RESEED=false

COPY bootstrap /opt/openldap/bootstrap
COPY entrypoint.sh /opt/openldap/entrypoint.sh
RUN chmod +x /opt/openldap/entrypoint.sh

EXPOSE 10389 10636

ENTRYPOINT ["/opt/openldap/entrypoint.sh"]
