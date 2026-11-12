MATCH (b:BankAccount)-[:SEND]->(t:MoneyTransfer)
WHERE t.amount > 900 AND t.transactionDate = '2018-02-07'
RETURN DISTINCT b.accountNumber