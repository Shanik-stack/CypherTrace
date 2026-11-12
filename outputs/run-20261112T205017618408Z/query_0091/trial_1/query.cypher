MATCH (v:Visit)-[:LOCATED_AT]->(p:Place)-[:PART_OF]->(r:Region)
RETURN r.name AS region,
       ROUND(100.0 * SUM(CASE WHEN p.type = 'Mall' THEN 1 ELSE 0 END) / COUNT(v), 2) AS mallVisitPercentage
ORDER BY region