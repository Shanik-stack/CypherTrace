MATCH (p:Purchase)-[:DELIVERED_AT]->(a:DeliveryAddress)
WHERE a.state = 'Montana'
RETURN avg(p.amount) AS averagePurchaseAmount