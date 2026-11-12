MATCH (a:AccountHolder)-[:HAS_ADDRESS]->(ad:Address), (a)-[:HAS_SSN]->(s:SSN)
WHERE ad.state = 'Wisconsin' AND s.ssn STARTS WITH '044'
RETURN DISTINCT a.fullName AS name