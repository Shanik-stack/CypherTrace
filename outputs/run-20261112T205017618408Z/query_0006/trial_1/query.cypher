MATCH (accountHolder:AccountHolder)-[:HAS_ADDRESS]->(address:Address)
MATCH (accountHolder)-[:HAS_BANKACCOUNT]->(bankAccount:BankAccount)
RETURN address.state AS state, avg(bankAccount.balance) AS averageBalance
ORDER BY state