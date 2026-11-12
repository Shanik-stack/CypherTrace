MATCH (person:Person)-[v:VISITS]->(p:Place)
OPTIONAL MATCH (p)-[:PART_OF]->(c:Country)
OPTIONAL MATCH (p)-[:PART_OF]->(:Region)-[:PART_OF]->(c2:Country)
WITH coalesce(c.name, c2.name) AS country,
  count(v) AS totalVisits,
  sum(CASE WHEN person.healthstatus = 'Sick' THEN 1 ELSE 0 END) AS sickVisits,
  avg(v.duration.inSeconds) AS averageDurationSeconds
RETURN country,
  toFloat(sickVisits) / toFloat(totalVisits) AS visitRatio,
  averageDurationSeconds