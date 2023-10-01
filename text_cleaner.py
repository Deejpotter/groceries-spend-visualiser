import openai


def clean_text(text):
    """
    Cleans the extracted text from PDF using OpenAI.

    Parameters:
        text (str): The extracted text.

    Returns:
        str: The cleaned text.
    """
    prompt = f""" Consider the following text:
    {text}
    
    Extract the words from that text. Some of the words may be misspelled or have too many letters. Some words may be missing. Some may be split into multiple words. Some may be joined together.
    Try to recreate the original text. Then format it nicely.    
    """

    # Use OpenAI's text generation to clean the text
    response = openai.Completion.create(
        engine="davinci",
        prompt=prompt,
        max_tokens=100,  # Adjust max_tokens as needed
        n=1,  # Number of completions to generate
        stop=None,  # Stop tokens to limit the response (can be a list of strings)
    )

    # Extract the cleaned text from the OpenAI response
    cleaned_text = response.choices[0].text.strip()

    return cleaned_text
