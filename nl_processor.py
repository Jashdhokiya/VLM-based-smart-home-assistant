import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

def parse_command(client, user_input):
    pre_prompt = (
        "Provide the output strictly as a dictionary with object names as keys and their counts as values. "
        "For example, if the input is '2 lights, one fan,' the output should be: {'light': 2, 'fan': 1}. "
        "Do not include any preamble or additional text."
    )
    prompt = pre_prompt + str(user_input)
    model = os.getenv("GROQ_LLM_MODEL", "qwen/qwen3.8-27b")
    response = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=model
    )
    return response.choices[0].message.content

def refine(command):
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    obj_dic = parse_command(client, command)
    return obj_dic
