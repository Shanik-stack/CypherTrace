MATCH (p:Person)-[v:VISITS]->(pl:Place)
WHERE pl.type = 'Grocery shop' OR pl.type = 'Restaurant'
WITH p, pl.type AS placeType, count(v) AS visitsPerPerson
WITH avg(CASE WHEN placeType = 'Grocery shop' THEN visitsPerPerson END) AS avgGroceryShopVisits, avg(CASE WHEN placeType = 'Restaurant' THEN visitsPerPerson END) AS avgRestaurantVisits
RETURN avgGroceryShopVisits AS avgGroceryShopVisits, avgRestaurantVisits AS avgRestaurantVisits, (avgGroceryShopVisits - avgRestaurantVisits) / ((avgGroceryShopVisits + avgRestaurantVisits) / 2.0) * 100 AS percentageDifference