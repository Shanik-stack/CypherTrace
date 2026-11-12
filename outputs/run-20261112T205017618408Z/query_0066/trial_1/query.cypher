MATCH (visit:Visit)-[:LOCATED_AT]->(place:Place)
WHERE visit.starttime < datetime('2020-04-26T00:00:00Z')
RETURN place.type AS placeType,
       count(*) AS totalVisitCount,
       avg(durationInSeconds(visit.duration)) AS averageDurationSeconds