MATCH (c:Country)-[:PART_OF*1..2]->(p:Place)<-[v:VISITS]-(person:Person)
OPTIONAL MATCH (person)-[:PERFORMS_VISIT]->(visit:Visit)-[:LOCATED_AT]->(p)
WITH c.name AS countryName,
  count(v) AS totalVisits,
  sum(CASE WHEN person.healthstatus = 'Sick' THEN 1 ELSE 0 END) AS sickVisits,
  avg(visit.duration.inSeconds) AS averageDurationSeconds
RETURN countryName,
  toFloat(sickVisits) / toFloat(totalVisits) AS visitRatio,
  averageDurationSeconds