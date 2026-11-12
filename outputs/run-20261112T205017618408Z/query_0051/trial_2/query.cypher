MATCH (a:AccountHolder)-[:HAS_UNSECUREDLOAN]->(u:UnsecuredLoan)
MATCH (a)-[:HAS_PHONENUMBER]->(p:PhoneNumber)
WHERE toFloat(u.APR) > 0.35 AND p.provider = 'Loud and Clear'
RETURN DISTINCT a.UniqueId AS uniqueId, a.firstName AS firstName, a.lastName AS lastName, a.fullName AS fullName
ORDER BY lastName, firstName