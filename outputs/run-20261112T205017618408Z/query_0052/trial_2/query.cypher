MATCH (ah:AccountHolder)-[:HAS_ADDRESS]->(a:Address)
MATCH (ah:AccountHolder)-[:HAS_CREDITCARD]->(cc:CreditCard)
WHERE a.state = 'Washington' AND cc.latePayments > 0.0
RETURN DISTINCT ah.UniqueId AS UniqueId, ah.fullName AS fullName, ah.birthDate AS birthDate
ORDER BY UniqueId