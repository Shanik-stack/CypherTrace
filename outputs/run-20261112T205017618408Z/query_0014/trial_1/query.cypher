MATCH (a:AccountHolder)-[:HAS_UNSECUREDLOAN]->(u:UnsecuredLoan)
MATCH (a)-[:HAS_ADDRESS]->(ad:Address)
RETURN ad.city AS city, avg(u.balance) AS averageBalance