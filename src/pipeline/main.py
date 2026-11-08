import json
from pathlib import Path
import pandas as pd

from customer_support_tickets import *

def main():
    
    # Part-A
    
    tickets_data_path = Path("data/tickets.json")
    
    with tickets_data_path.open() as f:
        tickets = json.load(f)
    df = pd.DataFrame(tickets)
    
    # cleaning tickets
    cleaned_df = clean_tickets(df)
    
    # obtaining analysis on tickets
    analysis_statistics = analyse_tickets(cleaned_df)
    print("\n Ticket Analysis results: \n", analysis_statistics, "\n")
    
    # Part B
    # Check out docs\ai_use_documentation.md for my LLM evaluation
    
    #Building Neo4j GraphDatabase from cleaned ticket data
    build_graph_from_tickets(cleaned_df)
    
    #Querying GraphDatabase for users that raised ticket for multiple distinct products
    query_results = find_multi_product_customers()
    print(f"\n Results of multi product customer query: \n {query_results} \n")

if __name__ == "__main__":
    main()
        