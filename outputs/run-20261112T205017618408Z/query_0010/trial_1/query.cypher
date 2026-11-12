MATCH (b:BankAccount)-[:FROM]->(f:FinancialInstitute)
RETURN f.name AS financialInstitute, avg(b.balance) AS averageBalance
ORDER BY averageBalance DESC