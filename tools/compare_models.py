import os
import sys
import json
import gc
from pathlib import Path
from mlx_lm import load, generate

def test_model(model_path, content, prompt_template):
    print(f"\n=======================================")
    print(f"Loading Model: {model_path.split('/')[-1]}")
    print(f"=======================================")
    model, tokenizer = load(model_path)
    
    # Simple prompt adaptation
    prompt = prompt_template.replace("{DOCUMENT_CONTENT}", content)
    messages = [{"role": "user", "content": prompt}]
    
    formatted_prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    
    print("\nGenerating chunking JSON...")
    response = generate(model, tokenizer, prompt=formatted_prompt, max_tokens=1024, verbose=False)
    
    print("\n--- OUTPUT ---")
    print(response)
    print("----------------\n")
    
    # Cleanup memory
    del model
    del tokenizer
    gc.collect()

if __name__ == "__main__":
    home = str(Path.home())
    qwen_path = os.path.join(home, ".lmstudio/models/lmstudio-community/Qwen3-Coder-Next-MLX-4bit")
    mistral_path = os.path.join(home, ".lmstudio/models/lmstudio-community/Mistral-Small-3.2-24B-Instruct-2506-MLX-4bit")
    
    test_text = """
[Voorzitter]: We gaan over tot het volgende agendapunt. Dat is het voorstel inzake de nieuwe Afvalstoffenverordening 2026. Het woord is aan de heer De Vries namens GroenLinks-PvdA.
[De Vries]: Voorzitter, dank. Voor onzr fractie is deze verordening een cruciale stap naar een circulaire stad. We hebben echter nog wel zorgen over de handhaving bij de ondergrondse containers. Kan de wethouder toezeggen dat hier extra op wordt gelet?
[Wethouder Zeegers]: Voorzitter. Dank aan de fractie voor de constatering dat dit een belangrijke stap is. Wat betreft de handhaving: we zetten al extra BOA's in, maar ik zal de directie Stadsbeheer vragen specifieke hotspots beter in de gaten te houden.
    """.strip()
    
    prompt_template = """
You are an expert Dutch archivist. Read the following political transcript and split it into logical chunks.
Return a JSON array where each object has:
- "title": A short Dutch title summarizing this specific point (max 10 words).
- "text": The literal excerpt.
- "questions": 3 questions in Dutch that the 'text' answers.

Return ONLY the raw JSON array.

### TRANSCRIPT
{DOCUMENT_CONTENT}
"""
    
    test_model(mistral_path, test_text, prompt_template)
    test_model(qwen_path, test_text, prompt_template)
