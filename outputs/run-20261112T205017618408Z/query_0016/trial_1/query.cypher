MATCH (m:MoneyTransfer)-[:WITH]->(p:PhoneNumber)
RETURN p.provider AS provider, avg(m.amount) AS averageAmount, count(m) AS moneyTransferCount
ORDER BY provider