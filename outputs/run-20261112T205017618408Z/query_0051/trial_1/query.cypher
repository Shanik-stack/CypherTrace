MATCH (a:AccountHolder)-[:HAS_UNSECUREDLOAN]->(u:UnsecuredLoan)
MATCH (a)-[:HAS_PHONENUMBER]->(p:PhoneNumber)
WHERE toFloat(u.APR) > 0.35 AND p.provider = 'Loud and Clear'
RETURN DISTINCT a.firstName AS firstName, a.lastName AS lastName, a.fullName AS fullName, a.UniqueId AS uniqueId, u.accountNumber AS loanAccountNumber, u.APR AS apr, p.phone AS phone
ORDER BY lastName, firstName