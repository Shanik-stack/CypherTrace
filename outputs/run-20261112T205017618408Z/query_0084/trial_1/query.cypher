MATCH (person:Person {healthstatus: 'Healthy'})-[:PERFORMS_VISIT]->(visit:Visit)-[:LOCATED_AT]->(place:Place)-[:PART_OF]->(:Region)-[:PART_OF]->(:Country)-[:PART_OF]->(continent:Continent)
WHERE place.type = 'Grocery shop'
RETURN continent.name AS continent, avg(duration.inSeconds(visit.duration)) AS averageVisitDurationSeconds
ORDER BY continent