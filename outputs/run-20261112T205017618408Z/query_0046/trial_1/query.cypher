MATCH (ah:AccountHolder)-[:HAS_PHONENUMBER]->(p:PhoneNumber {provider: 'B Tee'})
MATCH (ah:AccountHolder)-[:HAS_CREDITCARD]->(c:CreditCard {expirationDate: '06-16'})
RETURN DISTINCT ah.firstName, ah.lastName