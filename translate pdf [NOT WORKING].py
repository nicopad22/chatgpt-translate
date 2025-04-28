import pymupdf
import openai
import os

# VARIABLES
root = "C:\\Users\\nicol\\Downloads\\CHATGPT-TRANSLATE\\Excel\\BHPF 1\\"
output_language = "es"
# output_language = "en"

# OpenAI API client
# Store there your API key
with open(".\\TOKEN.txt") as token_file:
    client = openai.OpenAI(api_key=[line for line in token_file][0])
    
# Function to translate text
def translate_to_spanish(text):

    if text.strip() == "":
        return ""

    result = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
                {"role": "system",
                 "content": "Eres un asistente de traducción. Traduces cualquier texto que recibas al español, sin realizar comentarios adicionales."},
                {"role": "user", "content": f"{text.strip()}"}
            ],
        temperature=0.7,
    )

    translation = result.choices[0].message.content
    print(text + " / " + translation)

    return translation

# Function to translate text
def translate_to_english(text):

    if text.strip() == "":
        return ""
    
    result = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
                {"role": "system",
                 "content": "You are a translation assistant. You translate any text into english, without making any comments even if it is not translatable."},
                {"role": "user", "content": f"{text.strip()}"}
            ],
        temperature=0.7,
    )

    translation = result.choices[0].message.content
    print(text + " / " + translation)

    return translation

# List out all files in folder, filter out != .xlsx
files = os.listdir(root)
print(f"files found: ")
pdf_files = []
for file in files:
    if file[-4:] == ".pdf":
        print(" - " + file)
        pdf_files.append(file)

for file in pdf_files:
    newfile = file[:-4] + " [TRANSLATED].pdf"

    doc = pymupdf.open(root + file)

    for page in doc:
        # get original text info and store it

        page_dict = page.get_text("dict")

        page_text = []
        for block in page_dict["blocks"]:
            if block["type"] == 0:
                page_text.append(block)
        
        page.clean_contents()

        for block in page_text:
            for line in block["lines"]:
                for span in line["spans"]:
                    translated = ""
                    if output_language == "en":
                        translated = translate_to_english(span["text"])
                    elif output_language == "es":
                        translated = translate_to_spanish(span["text"])
                    
                    length = pymupdf.get_text_length(span["text"], span["font"], span["size"])
                    newlength = pymupdf.get_text_length(translated, span["font"], span["size"])

                    size = span["size"]

                    if newlength > length:
                        size = size * (length / newlength)

                    rect = pymupdf.Rect(span["bbox"])

                    try:
                        page.insert_textbox(rect, translated, fontsize=size, fontname=span["font"], color=span["color"])
                    except:
                        page.insert_textbox(rect, translated, fontsize=size, color=span["color"])

                        # DISCONTINUE
                        # ERAE MAS FACIL USAR ILOVE PDF Y EL TRADUCTOR DE WORDS

    doc.save(root + newfile)