MATCH (p:Purchase)-[:FROM_IP]->(ip:IP)-[:LOCATED_IN]->(s:State)
WHERE p.amount > 4000
RETURN DISTINCT s.name AS stateName