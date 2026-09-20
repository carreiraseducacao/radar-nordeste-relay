# radar-nordeste-relay
Relay do Radar Nordeste (Carreiras Educação): busca de um IP fora do Hostinger as fontes que bloqueiam o servidor
(FAMEM/MA via Siganet, Comperve/UFRN, AOCP) e publica `data/relay.json`, consumido por `fontes/relay.php` no radar.
Roda a cada 2h via GitHub Actions. Não contém segredos.
