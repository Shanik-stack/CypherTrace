MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(place:Place)-[:PART_OF]->(r:Region)
WITH r.name AS region, p.id AS personId, count(DISTINCT v) AS visitCount
WITH region, avg(visitCount) AS averageVisits, collect(visitCount) AS visitCounts
UNWIND visitCounts AS visitCount
WITH region AS regionName, averageVisits, sum(CASE WHEN visitCount > averageVisits THEN 1 ELSE 0 END) AS aboveAveragePersonCount
RETURN regionName, aboveAveragePersonCount
ORDER BY regionName