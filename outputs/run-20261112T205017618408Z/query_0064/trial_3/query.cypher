MATCH (c:Continent)<-[:PART_OF]-(co:Country)<-[:PART_OF]-(r:Region)<-[:PART_OF]-(pl:Place)<-[:LOCATED_AT]-(v:Visit)<-[:PERFORMS_VISIT]-(per:Person)
WITH c, count(DISTINCT v) AS totalVisits, count(DISTINCT CASE WHEN pl.type = 'Mall' AND per.healthstatus = 'Sick' THEN v END) AS sickMallVisits
RETURN c.name AS continent, toFloat(sickMallVisits) * 100.0 / toFloat(totalVisits) AS sickMallVisitPercentage
ORDER BY sickMallVisitPercentage DESC