MATCH (a:AccountHolder)-[:HAS_CREDITCARD]->(c:CreditCard)
WHERE a.birthDate >= '1976'
RETURN avg(c.balance) AS averageBalance