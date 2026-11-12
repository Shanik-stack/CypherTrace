MATCH (a:AccountHolder)-[:HAS_BANKACCOUNT]->(b:BankAccount)
WITH a, b
WHERE b.balance > 3000
MATCH (a)-[:HAS_CREDITCARD]->(c:CreditCard)
WHERE c.balance > 3000
RETURN DISTINCT a.fullName AS fullName