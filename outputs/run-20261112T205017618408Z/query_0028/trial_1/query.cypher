MATCH (f:Flagged {lastName: 'Pelyo'})-[:HAS_UNSECUREDLOAN]->(l:UnsecuredLoan)
RETURN avg(l.loanAmount) AS averageLoanAmount