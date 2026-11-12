MATCH (c:Continent)<-[:PART_OF]-(co:Country)<-[:PART_OF]-(r:Region)<-[:PART_OF]-(p:Place)<-[v:VISITS]-(person:Person)
WITH c, person, count(v) AS visitCount, sum(duration.inSeconds(v.duration)) AS personDurationSeconds
WITH c, sum(visitCount) AS totalVisits, count(person) AS personCount, sum(personDurationSeconds) AS totalDurationSeconds
RETURN c.name AS continent,
       toFloat(totalVisits) / toFloat(personCount) AS avgVisitsPerPerson,
       toFloat(totalDurationSeconds) / toFloat(totalVisits) AS avgVisitDurationSeconds
ORDER BY continent