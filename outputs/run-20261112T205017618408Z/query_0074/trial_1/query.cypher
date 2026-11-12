MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit)-[:LOCATED_AT]->(pl:Place)
WITH p.healthstatus AS healthstatus, pl.type AS placetype, p, count(v) AS visits
RETURN healthstatus, placetype, avg(visits) AS averageVisits