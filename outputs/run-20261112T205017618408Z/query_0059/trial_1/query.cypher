MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(:Place)-[:PART_OF]->(:Region)-[:PART_OF]->(:Country)-[:PART_OF]->(c:Continent)
WHERE v.duration > 3600
WITH c.name AS continent, count(v) AS totalVisits, count(DISTINCT p) AS persons, avg(v.duration) AS avgVisitDuration
RETURN continent, toFloat(totalVisits) / persons AS avgVisitsPerPerson, avgVisitDuration
ORDER BY continent