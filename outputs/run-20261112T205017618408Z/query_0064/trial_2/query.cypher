MATCH (c:Continent)<-[:PART_OF]-(co:Country)<-[:PART_OF]-(r:Region)<-[:PART_OF]-(pl:Place)<-[:LOCATED_AT]-(v:Visit)<-[:PERFORMS_VISIT]-(per:Person)
WITH c, count(DISTINCT v) AS totalVisits
OPTIONAL MATCH (c)<-[:PART_OF]-(co2:Country)<-[:PART_OF]-(r2:Region)<-[:PART_OF]-(pl2:Place)<-[:LOCATED_AT]-(v2:Visit)<-[:PERFORMS_VISIT]-(per2:Person)
WHERE pl2.type = 'Mall' AND per2.healthstatus = 'Sick'
RETURN c.name AS continent, toFloat(count(DISTINCT v2)) * 100.0 / toFloat(totalVisits) AS sickMallVisitPercentage
ORDER BY sickMallVisitPercentage DESC