import os

from dotenv import load_dotenv
from neo4j import GraphDatabase
import pandas as pd

load_dotenv()
uri = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
user, password = ("neo4j", os.environ["NEO4J_PASSWORD"])

# Databse connector function
def connect_to_database():
    driver =  GraphDatabase.driver(uri, auth=(user, password)) 
    driver.verify_connectivity()
    print("| Connection with database established |")
    return driver

def build_graph_from_tickets(df: pd.DataFrame):
    """
    MERGE: Only creates if missing, slower than CREATE, prevents duplicate nodes
    (): Node
    c, t, p: variables
    Customer, Ticket, Product: label
    name, ticket_id: node property
    [:]: relationship_type
    """
    
    # Query to create the entities in the ticket
    # SET after MERGE allows the merge to identify the ticket using the ticket_id and then set the properties
    create_entity_nodes = """
    MERGE (c:Customer {name: $customer})
    MERGE (t:Ticket {ticket_id: $ticket_id})
    SET t.status = $status,
        t.issue = $issue,
        t.created_at = $created_at,
        t.resolved_at = $resolved_at
    MERGE (p:Product {name: $product})
    """
    
    #Query to create relationship between the entities
    create_relationship_nodes = """
    MERGE (c)-[:RAISED]->(t)
    MERGE (t)-[:ABOUT]->(p)
    """
    
    merge_query = f"""{create_entity_nodes} {create_relationship_nodes}"""
    
    with connect_to_database() as driver:
        for row in df.itertuples(index=False):
            driver.execute_query(
                merge_query,
                customer=row.customer,
                ticket_id=row.ticket_id,
                product=row.product,
                status=row.status,
                issue=None if pd.isna(row.issue) else row.issue,
                created_at=row.created_at.to_pydatetime(),
                resolved_at=(
                    None
                    if pd.isna(row.resolved_at)
                    else row.resolved_at.to_pydatetime()
                ),
                database_="neo4j"
            )
    print("\n ----- Built Graph Database ----- \n")
    
    
def find_multi_product_customers():
    
    multi_product_customers_query = """MATCH (c:Customer)-[:RAISED]->(t:Ticket)-[:ABOUT]->(p:Product) 
    WITH c, count(DISTINCT p) AS product_count
    WHERE product_count>1 
    RETURN c.name as customer, product_count"""
    
    with connect_to_database() as driver:
        records, summary, keys = driver.execute_query(multi_product_customers_query, database_ = "neo4j")
        # print(f"Summary of query: {summary}")
        return [record.data() for record in records]
        
    
