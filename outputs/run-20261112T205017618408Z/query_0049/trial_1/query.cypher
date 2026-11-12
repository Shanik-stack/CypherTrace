MATCH (ah:AccountHolder)-[:HAS_BANKACCOUNT]->(ba:BankAccount), (ah)-[:HAS_CREDITCARD]->(cc:CreditCard)
WHERE ba.balance > 4000.0 AND cc.balance > 4000.0
RETURN DISTINCT ah