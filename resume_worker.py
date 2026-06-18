import os
from pypdf import PdfReader
from docx import Document

def extract_resume_text(file_path):
    """
    Extracts raw text strings from either PDF or DOCX formats safely.
    """
    if not file_path or not os.path.exists(file_path):
        return ""
    
    ext = os.path.splitext(file_path)[1].lower()
    text = ""
    
    try:
        if ext == ".pdf":
            reader = PdfReader(file_path)
            for page in reader.pages:
                text_content = page.extract_text()
                if text_content:
                    text += text_content + "\n"
        elif ext == ".docx":
            doc = Document(file_path)
            text += "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    except Exception as e:
        print(f"Resume extraction pipeline error: {e}")
        
    return text.strip()

def search_local_resume():
    """
    Scans the local working directory for any generic candidate resume.
    """
    valid_extensions = (".pdf", ".docx")
    for file in os.listdir("."):
        if any(file.lower().startswith(prefix) for prefix in ["resume", "cv"]):
            if file.lower().endswith(valid_extensions):
                return file
    return None