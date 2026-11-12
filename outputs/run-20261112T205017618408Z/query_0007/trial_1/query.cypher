MATCH (p:Purchase)-[:DELIVERED_AT]->(a:DeliveryAddress)
RETURN a.state AS state, avg(p.amount) AS averagePurchaseAmount
ORDER BY averagePurchaseAmount DESC