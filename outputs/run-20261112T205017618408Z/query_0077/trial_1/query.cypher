MATCH (sickPerson:Person {healthstatus: 'Sick'})-[:PERFORMS_VISIT]->(visit:Visit)-[:LOCATED_AT]->(place:Place)-[:PART_OF]->(:Region)-[:PART_OF]->(:Country)-[:PART_OF]->(continent:Continent)
RETURN continent.name AS ContinentName, place.type AS PlaceType, avg(visit.duration.inSeconds) AS AvgVisitDurationSeconds
ORDER BY ContinentName, PlaceType