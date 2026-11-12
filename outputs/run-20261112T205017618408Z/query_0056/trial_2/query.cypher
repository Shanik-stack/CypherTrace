MATCH (a:AccountHolder)-[:HAS_PHONENUMBER]->(p:PhoneNumber)
MATCH (a:AccountHolder)-[:HAS_CREDITCARD]->(c:CreditCard)
WHERE p.provider = 'B Tee' AND c.latePayments > 0
RETURN DISTINCT a.fullName