MATCH (b:BankAccount)-[:FROM]->(f:FinancialInstitute)
WHERE b.balance > 8000
WITH f, collect(b.accountNumber) AS accountNumbers
WHERE size(accountNumbers) >= 2
RETURN f.name AS financialInstitute, accountNumbers