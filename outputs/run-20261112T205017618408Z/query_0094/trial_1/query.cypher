MATCH (c:Continent)<-[:PART_OF]-(co:Country)<-[:PART_OF]-(r:Region)<-[:PART_OF]-(p:Place)<-[v:VISITS]-(person:Person)
WITH c, person, count(v) AS visitCount, sum(duration(v).seconds) AS personDurationSeconds
WITH c, avg(visitCount) AS avgVisitsPerPerson, sum(personDurationSeconds) / sum(visitCount) AS avgVisitDurationSeconds
RETURN c.name AS continent, avgVisitsPerPerson, avgVisitDurationSeconds
ORDER BY continent