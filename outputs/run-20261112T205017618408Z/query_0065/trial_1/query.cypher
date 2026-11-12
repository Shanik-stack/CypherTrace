MATCH (p:Person {healthstatus:'Sick'})-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(place:Place)-[:PART_OF]->(region:Region)-[:PART_OF]->(country:Country)-[:PART_OF]->(c:Continent)
WITH c, p, count(v) AS visitCount, sum(duration.inSeconds(v.duration)) AS totalDurationSeconds
WITH c, avg(visitCount) AS averageVisitCount, sum(totalDurationSeconds) / sum(visitCount) AS averageDurationSeconds
RETURN c.name AS continent, averageVisitCount, averageDurationSeconds
ORDER BY continent