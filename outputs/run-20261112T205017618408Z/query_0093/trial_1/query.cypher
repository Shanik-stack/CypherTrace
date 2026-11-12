MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)-[:PART_OF]->(r:Region)-[:PART_OF]->(c:Country)
WITH c.name AS country, count(v) AS totalVisits, sum(CASE WHEN v.starttime < datetime('2020-05-01T00:00:00Z') THEN 1 ELSE 0 END) AS beforeVisits
RETURN country, toFloat(beforeVisits) / toFloat(totalVisits) AS ratio
ORDER BY country