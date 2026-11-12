MATCH (a:AccountHolder)
OPTIONAL MATCH (a)-[:HAS_BANKACCOUNT]->(b:BankAccount)
RETURN a.UniqueId AS accountHolderUniqueId, a.fullName AS accountHolderFullName, avg(b.balance) AS averageBankAccountBalance, count(DISTINCT b) AS totalBankAccounts