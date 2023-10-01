import os
import openai
from text_extractor import extract_text_from_pdfs
from text_cleaner import clean_text
from data_extractor import extract_data
from data_analyzer import analyze_data


def initialize_openai():
    openai.api_key = os.getenv("OPENAI_API_KEY")


# Modify your process_invoices function to concatenate analysis results into a single string
def process_invoices(directory):
    """
    Processes all invoices in the specified directory.
    :param directory:
    :return:
    """
    # Initialize OpenAI
    initialize_openai()
    # Extract text from PDFs
    extracted_text = extract_text_from_pdfs(directory)

    # Clean the text
    cleaned_text = [clean_text(text) for text in extracted_text]

    # Extract data
    structured_data = [extract_data(text) for text in cleaned_text]

    # Analyze data
    analysis_results = [analyze_data(df) for df in structured_data]

    # Concatenate analysis results into a single string
    results_string = f"Extracted text: {extracted_text}\n\n Cleaned text: {cleaned_text}\n\n Structured data: {structured_data}\n\n Analysis results: {analysis_results}"

    return results_string

