MATCH (ah:AccountHolder)-[:HAS_CREDITCARD]->(cc:CreditCard), (ah)-[:HAS_ADDRESS]->(addr:Address)
RETURN addr.state AS state, avg(cc.balance) AS averageBalance
ORDER BY state