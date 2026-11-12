MATCH (a:AccountHolder)-[:HAS_ADDRESS]->(addr:Address)
MATCH (a)-[:HAS_CREDITCARD]->(c:CreditCard)
RETURN addr.state AS state, avg(c.balance) AS averageBalance
ORDER BY state