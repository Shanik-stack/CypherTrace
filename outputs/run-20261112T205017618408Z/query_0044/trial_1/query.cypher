MATCH (a:AccountHolder)-[:HAS_BANKACCOUNT]->(:BankAccount)
MATCH (a)-[:HAS_UNSECUREDLOAN]->(:UnsecuredLoan)
MATCH (a)-[:HAS_CREDITCARD]->(c:CreditCard)
WHERE c.balance > 300.0
RETURN DISTINCT a.firstName, a.lastName