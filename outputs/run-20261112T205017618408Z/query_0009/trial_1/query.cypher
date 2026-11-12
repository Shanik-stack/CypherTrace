MATCH (p:Purchase)-[:WITH_LOGIN]->(l:Login)-[:FOR_SHOP]->(s:Shop)
RETURN s.name AS shop, avg(p.amount) AS averagePurchaseAmount
ORDER BY shop