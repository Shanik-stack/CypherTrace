MATCH (person:Person)-[:PERFORMS_VISIT]->(visit:Visit)-[:LOCATED_AT]->(place:Place)
WHERE place.type = 'Mall'
WITH person.healthstatus AS healthStatus, avg(visit.duration.seconds) AS averageDurationSeconds
RETURN healthStatus, averageDurationSeconds