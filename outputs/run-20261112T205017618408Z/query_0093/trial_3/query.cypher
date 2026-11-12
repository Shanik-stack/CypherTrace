MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)-[:PART_OF]->(r:Region)-[:PART_OF]->(c:Country)
WITH c.name AS country, v
RETURN country, toFloat(sum(CASE WHEN v.starttime < datetime('2020-05-01T00:00:00Z') THEN 1 ELSE 0 END)) / toFloat(count(v)) AS ratio
ORDER BY country