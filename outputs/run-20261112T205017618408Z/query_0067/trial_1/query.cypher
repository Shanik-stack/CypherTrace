MATCH (v:Visit)-[:LOCATED_AT]->(p:Place)-[:PART_OF]->(r:Region)
MATCH (v)<-[:PERFORMS_VISIT]-(person:Person)
WITH r.name AS region, count(DISTINCT v) AS totalVisits, sum(CASE WHEN person.healthstatus = 'Sick' THEN 1 ELSE 0 END) AS sickVisits
RETURN region, toFloat(sickVisits) * 100.0 / totalVisits AS sickVisitPercentage
ORDER BY sickVisitPercentage DESC