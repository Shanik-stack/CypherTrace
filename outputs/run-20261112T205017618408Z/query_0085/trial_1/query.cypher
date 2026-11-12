MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(place:Place)-[:PART_OF]->(r:Region)
WITH r.name AS region, p.id AS personId, count(DISTINCT v) AS visitCount
WITH region, avg(visitCount) AS averageVisits, collect(visitCount) AS visitCounts
RETURN region AS regionName, size([visitCount IN visitCounts WHERE visitCount > averageVisits]) AS aboveAveragePersonCount
ORDER BY regionName