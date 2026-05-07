import openai
import os
from pathlib import Path
import shutil
import time
import excel_translate as xlsx
import ppt_translate as pptx
import word_translate as docx

# TODO: Handle all use cases in one program
root = Path("C:/Users/CPU/Downloads/traduccion/")
translation_tag = " [TRANSLATED]"
output_language = input("output language [es/en]: ")
# Store there your API key
client = 0
with open(Path(".") / "TOKEN.txt") as token_file:
    client = openai.OpenAI(api_key=[line for line in token_file][0])

def translate(text):
    if text.strip() == "":
        return ""

    translation = ""
    if output_language == "es":
        translation = translate_to_spanish(text)
    elif output_language == "en":
        translation = translate_to_english(text)
    
    print(text + " / " + translation)

    return translation

def translate_to_spanish(text):
    result = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[
                {"role": "system",
                 "content": "Eres un asistente de traducción. Traduces cualquier texto que recibas al español, sin realizar comentarios adicionales."},
                {"role": "user", "content": f"{text.strip()}"}
            ],
        temperature=0.7,
    )

    return result.choices[0].message.content

def translate_to_english(text):
    result = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[
                {"role": "system",
                 "content": "You are a translation assistant. You translate any text into english, without making any comments even if it is not translatable."},
                {"role": "user", "content": f"{text.strip()}"}
            ],
        temperature=0.7,
    )

    return result.choices[0].message.content

def main():
    # List out all files in folder, filter out != .xlsx
    files = list(root.iterdir())
    print(f"files found: ")
    excel_files = []
    pptx_files = []
    word_files = []
    for file in files:
        if file.suffix == ".xlsx" and not file.name.endswith(translation_tag + ".xlsx"):
            print(" - " + str(file))
            excel_files.append(file)
        elif file.suffix == ".pptx" and not file.name.endswith(translation_tag + ".pptx"):
            print(" - " + str(file))
            pptx_files.append(file)
        elif file.suffix == ".docx" and not file.name.endswith(translation_tag + ".docx"):
            print(" - " + str(file))
            word_files.append(file)
    
    counters = [0, 0, 0]

    print(f"\n\n-------------------------\nTRANSLATING EXCEL FILES\n-------------------------\n\n")
    for file in excel_files:
        print(f"\n\n-------------------------\nTRANSLATING: \"{file}\"\n-------------------------\n\n")
        outfile = file.with_stem(file.stem + translation_tag)
        if outfile.exists():
            continue

        shutil.copy(file, outfile)
        time.sleep(0.4)

        xlsx.translate_excel(str(root), str(file), str(outfile), translate)
        counters[0] += 1
    
    print(f"\n\n-------------------------\nTRANSLATING PPT FILES\n-------------------------\n\n")
    for file in pptx_files:
        print(f"\n\n-------------------------\nTRANSLATING: \"{file}\"\n-------------------------\n\n")
        outfile = file.with_stem(file.stem + translation_tag)
        if outfile.exists():
            continue

        shutil.copy(file, outfile)
        time.sleep(0.4)

        pptx.translate_ppt(str(root), str(file), str(outfile), translate)
        counters[1] += 1
    
    print(f"\n\n-------------------------\nTRANSLATING WORD FILES\n-------------------------\n\n")
    for file in word_files:
        print(f"\n\n-------------------------\nTRANSLATING: \"{file}\"\n-------------------------\n\n")
        outfile = file.with_stem(file.stem + translation_tag)
        if outfile.exists():
            continue

        shutil.copy(file, outfile)
        time.sleep(0.4)

        docx.translate_word(str(root), str(file), str(outfile), translate)
        counters[2] += 1

    print(f"-------------------------------------------------------\n\nDONE!\n\nTranslated a total of {counters[0]} excel files, {counters[1]} ppt files and {counters[2]} word files in the \"{str(root)}\" directory.\n")

if __name__ == "__main__":
    main()