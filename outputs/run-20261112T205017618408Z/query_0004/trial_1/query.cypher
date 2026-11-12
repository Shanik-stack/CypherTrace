MATCH (a:AccountHolder)-[:HAS_CREDITCARD]->(c:CreditCard)
WHERE a.birthDate < '1975'
RETURN avg(c.balance)