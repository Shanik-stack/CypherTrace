MATCH (mt:MoneyTransfer)-[:SEND]->(ba:BankAccount)-[:FROM]->(fi:FinancialInstitute)
MATCH (mt:MoneyTransfer)-[:WITH]->(p:PhoneNumber)
WHERE mt.amount > 500 AND p.provider = 'B Tee'
RETURN DISTINCT fi.name