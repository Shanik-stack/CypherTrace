MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)
WITH p.healthstatus AS healthStatus, p.id AS personId, count(v) AS visitCount, sum(duration.inSeconds(v.duration)) AS totalDurationSeconds
RETURN healthStatus, avg(visitCount) AS avgVisitCount, sum(totalDurationSeconds) / sum(visitCount) AS avgVisitDurationSeconds