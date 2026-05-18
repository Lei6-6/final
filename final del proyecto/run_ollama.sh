cd ollama-linux
screen -dmS serve_ollama bash -c 'bin/ollama serve'
screen -dmS run_ollama bash -c 'bin/ollama pull mistral'
# screen -dmS run_ollama bash -c 'bin/ollama pull qwen3-vl:8b'

# python3 -m venv venv
# source venv/bin/activate
# pip install ollama

