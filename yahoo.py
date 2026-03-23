# Updated yahoo.py

# Fixing the API key validation and updating model name

class GoogleGemini:
    def __init__(self, api_key):
        self.api_key = api_key
        self.validate_api_key()
        self.model_name = 'gemini-2.5-flash'

    def validate_api_key(self):
        if not self.api_key or len(self.api_key) < 40:  # Example validation
            raise ValueError("Invalid API key")

# Additional methods for Google Gemini functionality
