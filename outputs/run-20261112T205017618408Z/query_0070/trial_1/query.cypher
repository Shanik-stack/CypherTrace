MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)
WITH p.healthstatus AS healthStatus, p.id AS personId, count(v) AS visitCount, avg(duration.inSeconds(v.duration)) AS avgDurationPerVisit
RETURN healthStatus, avg(visitCount) AS avgVisitCount, avg(avgDurationPerVisit) AS avgVisitDurationSeconds