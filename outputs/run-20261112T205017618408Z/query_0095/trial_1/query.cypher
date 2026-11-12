MATCH (p:Person {healthstatus: 'Sick'})-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(:Place)-[:PART_OF]->(:Region)-[:PART_OF]->(:Country)-[:PART_OF]->(c:Continent)
WITH c, p, count(v) AS visitCount, sum(duration.inSeconds(v.duration)) AS personDurationSeconds
WITH c, avg(visitCount) AS averageNumberOfVisits, sum(personDurationSeconds) AS totalDurationSeconds, sum(visitCount) AS totalVisits
RETURN c.name AS continentName, averageNumberOfVisits AS averageNumberOfVisits, toFloat(totalDurationSeconds) / totalVisits AS averageVisitDurationSeconds