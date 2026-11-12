MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(place:Place)-[:PART_OF]->(r:Region)
WITH r.name AS region, p.id AS personId, count(DISTINCT v) AS visitCount
WITH region, collect(visitCount) AS visitCounts, avg(visitCount) AS averageVisits
RETURN region AS regionName, size([c IN visitCounts WHERE c > averageVisits]) AS aboveAveragePersonCount
ORDER BY regionName