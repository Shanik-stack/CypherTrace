from openai import OpenAI
from dotenv import load_dotenv
import os

#Open-AI agent to generate cypher queries
class OACypherAgent:
    def __init__(self, llm_service = "open_router"):
        load_dotenv()
        self.llm_service = llm_service
        
        if llm_service == "open_ai":
            self.api_key = os.getenv("OPENAI_API_KEY")
            self.model = str(os.getenv("OPENAI_MODEL"))
            self.client = OpenAI(api_key = self.api_key)
            
        
        elif llm_service =="open_router":
            self.api_key = os.getenv("OPENROUTER_API_KEY")
            self.model = str(os.getenv("OPENROUTER_MODEL"))
            self.client = OpenAI(api_key = self.api_key, base_url = ("https://openrouter.ai/api/v1"))
        
    
    def generate(self, base_prompt:str, batch:list[dict], batch_limit = 10):
        
        responses = []
        if len(batch)>batch_limit:
            raise ValueError("batch_size exceeds api price safety limit")
        
        
        for datapoint in batch:
            user_question = datapoint["question"]
            graph_schema = datapoint["schema"]
                
            response = self.client.responses.create(model = self.model, instructions = base_prompt, 
                                            input = (f"Graph Schema:\n{graph_schema}\n\n Question:\n{user_question}"),
                                            max_output_tokens = 1000)
            if not response.output_text:
                raise RuntimeError("Model did not return response")
            
            responses.append(response.output_text)
        return responses
        