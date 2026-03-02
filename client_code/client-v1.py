import os
from dataclasses import dataclass
from typing import List
from pydantic import BaseModel, Field
from agents import Agent, ModelSettings, OutputGuardrailTripwireTriggered, RunContextWrapper, Runner, output_guardrail, GuardrailFunctionOutput
from openai.types.shared import Reasoning



@dataclass
class UserContext:
    name: str
    uid: str
    number_of_questions_asked: int


class DenialResponse(BaseModel):
    denied: bool = Field(description="Whether the question was denied based on the number of questions asked.")
    reason: str = Field(description="The reason for denying the question, if applicable.")


class OutputObject(BaseModel):
    name: str = Field(description="Unique name for questiona and answer pair.")
    request: str = Field(description="The user's original request.")
    response: str = Field(description="The response to the user's request.")
    random_number: int = Field(description="A random number between 1 and 10.")
    denied: DenialResponse

def interact_with_context(context: RunContextWrapper[UserContext], agent: Agent[UserContext]) -> str:
    # Access context data
    user_name = context.context.name
    questions_asked = context.context.number_of_questions_asked

    # Update context data
    context.context.number_of_questions_asked += 1

    instructions = []
    instructions.append(f"You are a helpful assistant")
    instructions.append(f"If {questions_asked} is greater than 0 but less than 2, denie answering the question, otherwise answer the question. User name is {user_name}.")


    return "\n".join(instructions)




class GuardrailFunctionModel(BaseModel):
    denied: bool = Field(description="Whether the question was denied because it included a city in the restricted list.")
    reason: str = Field(description="The reason and city that caused the question to be denied, if applicable.")

def output_guardrail_instructions(context: RunContextWrapper[UserContext], agent: Agent[UserContext]) -> str:
    instructions = []

    instructions.append(f"You are a guardrail that determines whether a response to a question should be answered. ")
    instructions.append(f"If the answer includes any City in Illinois, deny the question. Otherwise, answer the question.")
    return "\n".join(instructions)

guardrail_agent = Agent(
    name="GuardrailAgent",
    model="gpt-5.2",
    model_settings=ModelSettings(reasoning=Reasoning(effort="medium", summary="detailed"), verbosity="medium"),
    output_type=GuardrailFunctionModel,
    instructions=output_guardrail_instructions
)



@output_guardrail
async def guardrail_function(context: RunContextWrapper[UserContext], agent: Agent[UserContext], output: OutputObject) -> GuardrailFunctionOutput:
    result = await Runner.run(guardrail_agent, output.response, context=context)
    
    return GuardrailFunctionOutput(output_info=result.final_output.reason, tripwire_triggered=result.final_output.denied)



def main() -> None:
    try:
        with open("key.file", "r", encoding="utf-8") as f:
            key = f.readline().strip()
        if not key:
            raise RuntimeError("key.file is empty.")
        os.environ["OPENAI_API_KEY"] = key
    except FileNotFoundError:
        raise RuntimeError("key.file not found.")

    agent = Agent[UserContext](
        name="SimpleAssistant",
        model="gpt-5.2",
        model_settings=ModelSettings(reasoning=Reasoning(effort="medium", summary="detailed"), verbosity="medium"),
        output_type=OutputObject,
        instructions=interact_with_context,
        output_guardrails=[guardrail_function]

    )

    prompt = "name 3 american cities?"
    context = UserContext(name="Alice", uid="12345", number_of_questions_asked=0)
    for _ in range(3):
        
        try:
            result = Runner.run_sync(agent, prompt, context=context)  # submit prompt and capture output
        
        except OutputGuardrailTripwireTriggered as e:
            print(f"Guardrail tripwire triggered: {e}")
            continue  # skip to the next iteration or handle as needed
        
        response_text = result.final_output

        print("Prompt:", prompt)
        print("Response:", response_text)
        print("Full response object:", result)

        print("Context:", result.context_wrapper.context)


if __name__ == "__main__":
    main()