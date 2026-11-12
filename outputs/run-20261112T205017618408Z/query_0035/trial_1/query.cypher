MATCH (p:Purchase)-[:WITH_LOGIN]->(l:Login {firstName: 'Marchelle'})-[:FOR_SHOP]->(s:Shop)
WHERE p.amount > 4000 AND p.date = '2018-02-07'
RETURN DISTINCT s.name