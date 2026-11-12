MATCH (v:Visit)-[:LOCATED_AT]->(p:Place)-[:PART_OF]->(r:Region)
WITH r, COUNT(v) AS totalVisits
OPTIONAL MATCH (mv:Visit)-[:LOCATED_AT]->(mp:Place {type: 'Mall'})-[:PART_OF]->(r)
WITH r.name AS region, totalVisits, COUNT(mv) AS mallVisits
RETURN region, ROUND(100.0 * mallVisits / totalVisits, 2) AS mallVisitPercentage
ORDER BY region