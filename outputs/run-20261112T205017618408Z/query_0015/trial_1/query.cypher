MATCH (b:BankAccount)-[:FROM]->(f:FinancialInstitute)
WHERE f.name STARTS WITH 'Muller'
RETURN avg(b.balance) AS averageBalance, count(b) AS totalBankAccounts