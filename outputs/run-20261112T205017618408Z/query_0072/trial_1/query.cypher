MATCH (p:Person {healthstatus:'Sick'})-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)-[:PART_OF]->(r:Region)
WITH r.name AS region, p.id AS personId, count(v) AS visitCount, sum(duration.inSeconds(v.duration)) AS totalSeconds
WITH region, avg(visitCount) AS avgVisits, sum(totalSeconds) / sum(visitCount) AS avgVisitDurationSeconds
RETURN region, avgVisits, avgVisitDurationSeconds