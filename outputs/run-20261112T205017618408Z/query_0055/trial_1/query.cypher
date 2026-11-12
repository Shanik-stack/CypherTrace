MATCH (b:BankAccount)-[:FROM]->(f:FinancialInstitute)
WHERE b.balance > 2000
WITH f, count(b) AS qualifyingAccounts
WHERE qualifyingAccounts >= 2
RETURN f.name AS financialInstituteName
ORDER BY financialInstituteName