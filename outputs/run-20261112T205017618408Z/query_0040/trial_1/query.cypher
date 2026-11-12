MATCH (s:Shop)<-[:FOR_SHOP]-(l:Login)<-[:WITH_LOGIN]-(p:Purchase)-[:FROM_IP]->(ip:IP)-[:LOCATED_IN]->(st:State {name: 'Arkansas'})
RETURN DISTINCT s.name AS shopName