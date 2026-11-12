MATCH (ah:AccountHolder)-[:HAS_BANKACCOUNT]->(ba:BankAccount)
WHERE ba.balance > 7000
MATCH (ah)-[:HAS_CREDITCARD]->(cc:CreditCard)
WHERE cc.limit > 50000
RETURN DISTINCT ah.fullName AS fullName