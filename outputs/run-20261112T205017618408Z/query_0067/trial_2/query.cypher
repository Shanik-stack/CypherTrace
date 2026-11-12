MATCH (v:Visit)-[:LOCATED_AT]->(p:Place)-[:PART_OF]->(r:Region)
MATCH (v)<-[:PERFORMS_VISIT]-(person:Person)
WITH r.name AS region, count(DISTINCT v) AS totalVisits, count(DISTINCT CASE WHEN person.healthstatus = 'Sick' THEN v END) AS sickVisits
RETURN region, toFloat(sickVisits) * 100.0 / totalVisits AS sickVisitPercentage
ORDER BY sickVisitPercentage DESC