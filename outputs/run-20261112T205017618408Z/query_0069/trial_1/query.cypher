MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)-[:PART_OF]->(r:Region)-[:PART_OF]->(c:Country)
WHERE p.confirmedtime < datetime('2020-04-21T00:00:00Z')
RETURN c.name AS country, avg(duration.inSeconds(v.duration)) AS averageVisitDurationInSeconds
ORDER BY country