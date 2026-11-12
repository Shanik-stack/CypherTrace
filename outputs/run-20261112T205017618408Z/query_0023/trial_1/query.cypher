MATCH (b:BankAccount)-[:FROM]->(fi:FinancialInstitute)
RETURN fi.name AS financialInstitute, avg(b.balance) AS averageBalance, count(b) AS totalBankAccounts
ORDER BY financialInstitute