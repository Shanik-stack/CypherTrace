MATCH (v:Visit)-[:LOCATED_AT]->(p:Place)-[:PART_OF]->(r:Region)
WITH r, COUNT(v) AS totalVisits, SUM(CASE WHEN p.type = 'Mall' THEN 1 ELSE 0 END) AS mallVisits
RETURN r.name AS region, ROUND(100.0 * mallVisits / totalVisits, 2) AS mallVisitPercentage
ORDER BY region