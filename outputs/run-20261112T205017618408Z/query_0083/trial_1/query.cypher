MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)
WITH pl.type AS placeType, avg(v.duration.toSeconds) AS avgVisitDurationSeconds, count(DISTINCT p) AS distinctPersons
RETURN placeType, avgVisitDurationSeconds, distinctPersons
ORDER BY placeType