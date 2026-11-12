MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(:Place)-[:PART_OF]->(:Region)-[:PART_OF]->(:Country)-[:PART_OF]->(c:Continent)
WITH c, p, v, duration.inSeconds(v.duration) AS seconds
WHERE seconds > 3600
WITH c.name AS continent, count(v) AS totalVisits, count(DISTINCT p) AS persons, avg(seconds) AS avgVisitDuration
RETURN continent, toFloat(totalVisits) / persons AS avgVisitsPerPerson, avgVisitDuration
ORDER BY continent