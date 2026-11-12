MATCH (a:AccountHolder {lastName: 'Breckenridge'})-[:HAS_CREDITCARD]->(c:CreditCard)
RETURN avg(c.balance) AS averageBalance, sum(c.limit) AS totalLimit