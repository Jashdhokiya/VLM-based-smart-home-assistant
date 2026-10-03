import os
import base64
from dotenv import load_dotenv

load_dotenv()

def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

def information(object_list, image_path: str = "./annotated_image.jpg") -> str:
    """
    Performs spatial reasoning on annotated objects.
    Defaults to Groq (Vision or LLM) so only GROQ_API_KEY is needed.
    """
    groq_api_key = os.getenv("GROQ_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Annotated image file '{image_path}' not found for spatial inference.")

    base64_image = encode_image(image_path)
    prompt = (
        f"Analyze the provided image and the annotations {object_list}, ensuring the response does not exceed 1000 words. "
        "Summarize the spatial placement of each annotated IoT device with the following key details:\n\n"
        "**1. Object Location:**\n"
        "   - Briefly describe each device's position relative to major room features (e.g., walls, windows, doors, furniture, desk).\n"
        "**2. Nearby Objects:**\n"
        "   - Identify the closest objects (IoT and non-IoT) and summarize their influence on placement.\n"
        "**3. Spatial Relationships:**\n"
        "   - Note relative depth, alignment (e.g. left vs right), and positioning concisely, prioritizing only the most relevant details.\n\n"
        "Keep descriptions brief, precise, and within the word limit while maintaining clarity."
    )

    # If user explicitly opted for OpenAI and provided key
    if os.getenv("AI_PROVIDER") == "openai" and openai_api_key and openai_api_key != "...":
        from openai import OpenAI
        endpoint = os.getenv("OPENAI_BASE_URL", "https://models.inference.ai.azure.com")
        model_name = os.getenv("OPENAI_SPATIAL_MODEL", "gpt-4o")
        client = OpenAI(base_url=endpoint, api_key=openai_api_key) if endpoint else OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                ],
            }],
            temperature=0.7,
            max_tokens=1000,
            model=model_name
        )
        return response.choices[0].message.content

    # Primary: Groq Vision / LLM
    from groq import Groq
    if not groq_api_key or groq_api_key == "...":
        raise ValueError("GROQ_API_KEY is not configured in .env")

    groq_client = Groq(api_key=groq_api_key)
    vision_model = os.getenv("GROQ_VISION_MODEL")
    if vision_model and vision_model != "..." and vision_model != "":
        try:
            print(f"[SPATIAL] Analyzing image using Groq Vision ({vision_model})...")
            response = groq_client.chat.completions.create(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                    ],
                }],
                temperature=0.7,
                max_tokens=1000,
                model=vision_model
            )
            return response.choices[0].message.content
        except Exception as vision_err:
            print(f"[SPATIAL] [WARN] Groq vision returned: {vision_err}. Using text spatial reasoning...")

    # Text spatial reasoning using Qwen
    text_model = os.getenv("GROQ_LLM_MODEL", "qwen/qwen3.8-27b")
    print(f"[SPATIAL] Reasoning device placements with Groq ({text_model})...")
    text_prompt = (
        f"You are a spatial reasoning assistant for an IoT smart home system.\n"
        f"The scene has the following detected devices: {object_list}.\n"
        f"The devices are numbered sequentially from left to right across the camera frame (Light1 is on the left, Light2 is on the right).\n"
        "Describe the spatial placement and relationships for each device in the room (e.g. Light1 on the left side / near desk; Light2 on the right side / near window or door).\n"
        "Provide clear, practical spatial guidelines so command decisions like 'turn on the light near the window' or 'the leftmost light' map accurately."
    )
    res = groq_client.chat.completions.create(
        messages=[{"role": "user", "content": text_prompt}],
        temperature=0.3,
        max_tokens=800,
        model=text_model
    )
    return res.choices[0].message.content