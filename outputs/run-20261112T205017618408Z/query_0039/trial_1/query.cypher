MATCH (ah:AccountHolder)-[:HAS_BANKACCOUNT]->(ba:BankAccount), (ah)-[:HAS_CREDITCARD]->(cc:CreditCard)
WHERE ba.balance > 1000 AND cc.balance > 1000
WITH DISTINCT ah
RETURN ah.fullName AS name
ORDER BY ah.lastName