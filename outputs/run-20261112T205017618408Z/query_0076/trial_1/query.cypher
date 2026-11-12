MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)-[:PART_OF]->(rg:Region)-[:PART_OF]->(c:Country)
WHERE v.starttime < p.confirmedtime
WITH c.name AS country_name, p.healthstatus AS health_status, p, count(DISTINCT v) AS pre_confirmation_visits
RETURN country_name, health_status, avg(pre_confirmation_visits) AS avg_pre_confirmation_visits
ORDER BY country_name, health_status