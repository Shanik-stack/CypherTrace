MATCH (p:Purchase)-[:FROM_IP]->(ip:IP)-[:LOCATED_IN]->(s:State)
RETURN s.name AS state, avg(p.amount) AS averagePurchaseAmount
ORDER BY averagePurchaseAmount DESC