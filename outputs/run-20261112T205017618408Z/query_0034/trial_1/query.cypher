MATCH (ah:AccountHolder)-[:HAS_BANKACCOUNT]->(ba:BankAccount)-[:FROM]->(fi:FinancialInstitute)
WHERE fi.name = 'Padberg and Sons'
MATCH (ah)-[:HAS_CREDITCARD]->(cc:CreditCard)
WHERE cc.balance > 3000
RETURN ah