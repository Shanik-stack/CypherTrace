MATCH (fi:FinancialInstitute)<-[:FROM]-(ba:BankAccount)-[:SEND]->(mt:MoneyTransfer)
WHERE mt.amount > 850
WITH fi, count(DISTINCT ba) AS accountCount
WHERE accountCount >= 2
RETURN DISTINCT fi.name