MATCH (p:Person)-[v:VISITS]->(pl:Place)
WHERE pl.type IN ['Grocery shop', 'Restaurant']
WITH p, pl.type AS placeType, count(v) AS visitsPerPerson
WITH avg(CASE WHEN placeType = 'Grocery shop' THEN visitsPerPerson END) AS avgGroceryShopVisits, avg(CASE WHEN placeType = 'Restaurant' THEN visitsPerPerson END) AS avgRestaurantVisits
RETURN avgGroceryShopVisits, avgRestaurantVisits, (avgGroceryShopVisits - avgRestaurantVisits) / ((avgGroceryShopVisits + avgRestaurantVisits) / 2) * 100 AS percentageDifference