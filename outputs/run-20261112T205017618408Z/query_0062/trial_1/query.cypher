MATCH (p:Person)-[:PERFORMS_VISIT]->(v:Visit), (v)-[:LOCATED_AT]->(pl:Place), (pl)-[:PART_OF]->(r:Region)
WHERE v.starttime < p.confirmedtime
WITH r, p, count(DISTINCT v) AS preConfirmationVisits
RETURN r.name AS region, avg(preConfirmationVisits) AS avgPreConfirmationVisits