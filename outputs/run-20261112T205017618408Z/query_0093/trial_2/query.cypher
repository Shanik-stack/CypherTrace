MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)-[:PART_OF]->(r:Region)-[:PART_OF]->(c:Country)
WITH c.name AS country, collect(v.starttime) AS times
RETURN country, toFloat(size([t IN times WHERE t < datetime('2020-05-01T00:00:00Z')])) / toFloat(size(times)) AS ratio
ORDER BY country