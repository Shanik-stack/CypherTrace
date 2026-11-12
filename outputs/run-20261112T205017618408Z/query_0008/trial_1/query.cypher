MATCH (p:Purchase)-[:WITH_CARD]->(c:BankCard)
RETURN c.expirationDate AS expirationDate, sum(p.amount) AS totalPurchaseAmount
ORDER BY expirationDate