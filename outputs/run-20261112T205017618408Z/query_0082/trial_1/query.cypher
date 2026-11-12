MATCH (p:Person)-[v:VISITS]->(pl:Place)-[:PART_OF]->(r:Region)-[:PART_OF]->(co:Country)-[:PART_OF]->(c:Continent)
RETURN c.name AS continent, pl.type AS placeType, avg(duration.inSeconds(v.duration)) AS averageDuration
ORDER BY continent ASC, averageDuration DESC