MATCH (p:Purchase)-[:WITH_CARD]->(c:BankCard)
WHERE c.limit > 5000
RETURN c.expirationDate AS expirationDate, avg(p.amount) AS averageAmount
ORDER BY expirationDate