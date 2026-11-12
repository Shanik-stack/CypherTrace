MATCH (c:Continent)<-[:PART_OF]-(co:Country)<-[:PART_OF]-(r:Region)<-[:PART_OF]-(pl:Place)<-[:LOCATED_AT]-(v:Visit)<-[:PERFORMS_VISIT]-(per:Person)
WITH c.name AS continent, count(v) AS totalVisits, sum(CASE WHEN per.healthstatus = 'Sick' AND pl.type = 'Mall' THEN 1 ELSE 0 END) AS sickMallVisits
RETURN continent, toFloat(sickMallVisits) * 100.0 / toFloat(totalVisits) AS sickMallVisitPercentage
ORDER BY sickMallVisitPercentage DESC