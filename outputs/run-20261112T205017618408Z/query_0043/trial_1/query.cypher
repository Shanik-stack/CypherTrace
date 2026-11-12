MATCH (a:AccountHolder)-[:HAS_ADDRESS]->(addr:Address)
WHERE a.fullName = 'Jorge Girod' OR (a.firstName = 'Jorge' AND a.lastName = 'Girod')
MATCH (f:Flagged)-[:HAS_ADDRESS]->(addr)
RETURN DISTINCT f.fullName