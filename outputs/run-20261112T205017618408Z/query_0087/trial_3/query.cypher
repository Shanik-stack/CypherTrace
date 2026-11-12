CALL {
  MATCH (p:Person)-[v:VISITS]->(pl:Place)
  WHERE pl.type = 'Grocery shop'
  WITH p, count(v) AS visitsPerPerson
  RETURN avg(visitsPerPerson) AS avgGroceryShopVisits
}
CALL {
  MATCH (p:Person)-[v:VISITS]->(pl:Place)
  WHERE pl.type = 'Restaurant'
  WITH p, count(v) AS visitsPerPerson
  RETURN avg(visitsPerPerson) AS avgRestaurantVisits
}
RETURN avgGroceryShopVisits AS avgGroceryShopVisits, avgRestaurantVisits AS avgRestaurantVisits, (avgGroceryShopVisits - avgRestaurantVisits) / ((avgGroceryShopVisits + avgRestaurantVisits) / 2.0) * 100 AS percentageDifference