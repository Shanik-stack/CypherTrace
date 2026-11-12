MATCH (mt:MoneyTransfer)-[:WITH]->(p:PhoneNumber)
WHERE p.provider = 'Interstate Mob'
RETURN avg(mt.amount) AS averageAmount, count(mt) AS totalTransfers