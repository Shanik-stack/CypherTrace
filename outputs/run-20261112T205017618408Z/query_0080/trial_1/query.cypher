MATCH (p:Person {healthstatus: 'Healthy'})-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)-[:PART_OF]->(r:Region)
WITH r, avg(duration.inSeconds(v.duration)) AS avg_duration, count(v) AS total_visits
RETURN r.name AS region_name, avg_duration, total_visits