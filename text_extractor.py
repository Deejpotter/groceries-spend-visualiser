import os
import pdfplumber


def extract_text_from_pdfs(invoice_dir):
    """
    Extracts text from all PDF files in the specified directory using pdfplumber.

    Parameters:
        invoice_dir (str): The directory containing PDF files.

    Returns:
        list: A list of strings, each representing the text extracted from a PDF file.
    """
    all_text = []
    for filename in os.listdir(invoice_dir):
        if filename.endswith('.pdf'):
            filepath = os.path.join(invoice_dir, filename)
            try:
                with pdfplumber.open(filepath) as pdf:
                    text = ''
                    for page in pdf.pages:
                        text += page.extract_text()
                    all_text.append(text)
            except Exception as e:
                print(f"An error occurred while processing {filename}: {e}")
    return all_text


if __name__ == '__main__':
    directory = 'invoices'  # Directory where your PDFs are stored
    extracted_text = extract_text_from_pdfs(directory)
    print(extracted_text)  # Print the extracted text to verify the output
