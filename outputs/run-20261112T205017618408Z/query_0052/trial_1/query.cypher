MATCH (ah:AccountHolder)-[:HAS_ADDRESS]->(a:Address)-[:HAS_CREDITCARD]->(cc:CreditCard)
MATCH (ah:AccountHolder)-[:HAS_CREDITCARD]->(cc:CreditCard)
WHERE a.state = 'Washington' AND cc.latePayments > 0.0
RETURN DISTINCT ah.fullName AS fullName, ah.birthDate AS birthDate
ORDER BY fullName