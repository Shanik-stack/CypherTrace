MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)
WITH p.healthstatus AS healthStatus, p.id AS personId,
     count(v) AS visitCount,
     sum(v.duration.days * 86400 + v.duration.hours * 3600 + v.duration.minutes * 60 + v.duration.seconds) AS totalDurationSeconds
RETURN healthStatus, avg(visitCount) AS avgVisitCount,
       toFloat(sum(totalDurationSeconds)) / sum(visitCount) AS avgVisitDurationSeconds