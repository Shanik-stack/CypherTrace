MATCH (v:Visit)-[:LOCATED_AT]->(p:Place)-[:PART_OF]->(r:Region)
MATCH (v)<-[:PERFORMS_VISIT]-(person:Person)
WITH r.name AS region, count(DISTINCT v) AS totalVisits
MATCH (sv:Visit)-[:LOCATED_AT]->(sp:Place)-[:PART_OF]->(sr:Region)
MATCH (sv)<-[:PERFORMS_VISIT]-(sickPerson:Person)
WHERE sr.name = region AND sickPerson.healthstatus = 'Sick'
WITH region, totalVisits, count(DISTINCT sv) AS sickVisits
RETURN region, toFloat(sickVisits) * 100.0 / totalVisits AS sickVisitPercentage
ORDER BY sickVisitPercentage DESC