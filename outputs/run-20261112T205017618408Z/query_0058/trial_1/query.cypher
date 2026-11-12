MATCH (c:Country)<-[:PART_OF]-(:Region)<-[:PART_OF]-(pl:Place)<-[:LOCATED_AT]-(v:Visit)<-[:PERFORMS_VISIT]-(p:Person)
WHERE p.healthstatus = 'Sick'
WITH c, pl.type AS placeType, v.duration.seconds AS secs
RETURN c.name AS country, avg(CASE WHEN placeType = 'Grocery shop' THEN secs END) AS groceryShopAvgSeconds, avg(secs) AS allPlacesAvgSeconds
ORDER BY country