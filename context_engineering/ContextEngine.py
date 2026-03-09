



from pydantic import BaseModel, Field


class HistorySummary(BaseModel):
    summary: str | None = Field(
        description="",
        default_factory= lambda: None
    )
    index: int
    
    
    def __repr__(self):
        return f"HistorySummary({self.index})"
    
class HistoryEntry(BaseModel):
    request: str
    response: str
    index: int
    
    def __str__(self):
        return f"HistoryEntry()"

    def __repr__(self):
        return f"HistoryEntry({self.index})"
    
class MemoryModel(BaseModel):
    data: list[list[HistoryEntry|HistorySummary]] = Field(
        description="The memory of the conversation.",
        default_factory=lambda: [[None for _ in range(5)]] + [[None for _ in range(2)] for _ in range(3)] + [[None]]
    )

class ContextModel(BaseModel):
    history: MemoryModel = Field(
        description="The history of the conversation. This is a list of turns, where each turn",
        default_factory=MemoryModel
    )

class Memory():
        
    def __init__(self, model):
        self.memory = model
        self.index = 0
        
    def create_history_summary(self, data: list[HistoryEntry]) -> HistorySummary:
        self.index +=1
        return HistorySummary(index=self.index)
    
    
    def free_space(self, bucket: int) -> bool:
        return any(entry is None for entry in self.memory.data[bucket])
    
    def clean_bucket(self, bucket: int) -> None:
        for i in range(len(self.memory.data[bucket])):
            self.memory.data[bucket][i] = None


    def find_open_space(self, bucket) -> int:
        for i, entry in enumerate(reversed(self.memory.data[bucket])):
            if entry is None:
                return len(self.memory.data[bucket]) - 1 - i
        raise ValueError("No open space in bucket")
       
    def insert_item(self, bucket: int, data):
        if not self.free_space(bucket):
            
            if bucket == len(self.memory.data) - 1:
                self.clean_bucket(bucket)
            else:
                summary = self.create_history_summary(bucket)
                self.clean_bucket(bucket)
                self.insert_item(bucket + 1, summary)

        open_space = self.find_open_space(bucket)
        self.memory.data[bucket][open_space] = data
                
    def add_history_entry(self, request: str, response: str) -> None:
        new_entry = HistoryEntry(request=request, response=response, index=self.index)
        self.insert_item(0, new_entry)
        self.index+=1
                
class ContextEngine:

    def __init__(self):
        self.context = ContextModel()      
        self.memory = Memory(self.context.history)

    def add_history_entry(self, request: str, response: str) -> None:
        self.memory.add_history_entry(request, response)

class Context():

    def __init__(self):
        self.context_engine = ContextEngine()

        
    def append_history(self, request: str, response: str) -> None:
        self.context_engine.add_history_entry(request, response)
        
if __name__ == "__main__":
    context_engine = ContextEngine()
    
    import sys
    from pathlib import Path
    from agents import Agent, Runner, set_tracing_disabled
    import json
    import textwrap
    import time

    
    def maybe_load_agents_locally() -> None:
        """Allow running from this repo without pip installing openai-agents."""
        if "agents" in sys.modules:
            return
        local_src = Path(__file__).resolve().parents[1] / "openai-agents-python" / "src"
        if local_src.exists():
            sys.path.insert(0, str(local_src))
    
    maybe_load_agents_locally()
    
    
    client_instructions = {}
    client_instructions["role"] = textwrap.dedent(
        """
        You are a conversational agent. 
        reply with a single word
        """
    )
    
    server_instructions = {}
    server_instructions["role"] = textwrap.dedent(
        """
        You are a conversational agent. 
        reply with a single word
        """
    )
    
    
    class TestAgentOutput(BaseModel):
        response: str = Field(
            description="The response to the prompt. This should be a single sentence."
        )


    client_agent: Agent[Context] = Agent(
        name="Client Side of Conversation",
        model="gpt-5-mini",
        instructions=str(json.dumps(client_instructions)),
        output_type=TestAgentOutput,
    )
    
    client_context = Context()

    server_agent = Agent(
        name="Server Side of Conversation",
        model="gpt-5-mini",
        instructions=str(json.dumps(server_instructions)),
        output_type=TestAgentOutput,
    )

    set_tracing_disabled(True)
    server_prompt = "hello"
    client_prompt = ""
    turns = 30

    for _ in range(turns):
        
        #print(f"Server Prompt: {server_prompt}\n\n")
        result = Runner.run_sync(server_agent, server_prompt)
        
        client_context.append_history(server_prompt, result.final_output.response)
        client_prompt = result.final_output.response
        #print(f"Client Prompt: {client_prompt}\n\n")
        result = Runner.run_sync(client_agent, client_prompt, context=client_context)
        print(result.context_wrapper.usage)
        server_prompt = result.final_output.response
        
        