MATCH (mt:MoneyTransfer)-[:SEND]->(ba:BankAccount)-[:FROM]->(fi:FinancialInstitute)-[:FROM]<-[:SEND]-(mt:MoneyTransfer)
MATCH (mt:MoneyTransfer)-[:WITH]->(p:PhoneNumber)
WHERE mt.amount > 500 AND p.provider = 'B Tee'
RETURN DISTINCT fi.name