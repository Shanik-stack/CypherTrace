MATCH (ah:AccountHolder)-[:HAS_ADDRESS]->(a:Address), (ah)-[:HAS_UNSECUREDLOAN]->(l:UnsecuredLoan)
WHERE a.state = 'West Virginia' AND l.loanAmount > 9000
RETURN DISTINCT ah.fullName