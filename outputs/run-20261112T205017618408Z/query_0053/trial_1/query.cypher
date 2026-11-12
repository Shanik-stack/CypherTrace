MATCH (ah:AccountHolder)-[:HAS_BANKACCOUNT]->(ba:BankAccount)-[:FROM]->(fi:FinancialInstitute)
WHERE fi.name = 'Dooley, Dooley and Dooley'
MATCH (p:Purchase)-[:WITH_LOGIN]->(l:Login)-[:FOR_SHOP]->(s:Shop)
WHERE s.business = 'deliver wireless niches'
AND l.firstName = ah.firstName
AND l.lastName = ah.lastName
RETURN DISTINCT ah.fullName