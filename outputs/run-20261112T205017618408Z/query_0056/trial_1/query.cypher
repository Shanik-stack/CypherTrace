MATCH (a:AccountHolder)-[:HAS_PHONENUMBER]->() RETURN a.fullName

Wait—output only the final query:

MATCH (a:AccountHolder)-[:HAS_PHONENUMBER]->(p:PhoneNumber)-[:HAS_PHONENUMBER]->(l:UnsecuredLoan)
MATCH (a:AccountHolder)-[:HAS_CREDITCARD]->(c:CreditCard)
WHERE p.provider = 'B Tee' AND c.latePayments > 0
RETURN DISTINCT a.fullName