MATCH (ba:BankAccount)
WITH avg(ba.balance) AS avgAccountBalance
OPTIONAL MATCH (ah:AccountHolder)-[:HAS_UNSECUREDLOAN]->(ul:UnsecuredLoan)
RETURN avgAccountBalance, avg(ul.loanAmount) AS avgUnsecuredLoanAmount