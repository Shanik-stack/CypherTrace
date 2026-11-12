MATCH (v:Visit)-[:LOCATED_AT]->(:Place)-[:PART_OF]->(:Region)-[:PART_OF]->(:Country)-[:PART_OF]->(c:Continent)
RETURN c.name AS continent, count(v) AS totalVisits, avg(duration.inSeconds(v.duration)) AS averageDurationInSeconds
ORDER BY totalVisits DESC