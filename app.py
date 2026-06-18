from flask import Flask, request, jsonify, send_file
from openai import OpenAI
import json
import re
import os  # <-- ADD THIS MISSING IMPORT HERE

from config import GEMINI_API_KEY
from resume_worker import extract_resume_text
app = Flask(__name__)

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=GEMINI_API_KEY,
)

MODEL_NAME = "google/gemini-2.5-flash"

# In-memory history cache to build the final report
session_history = []
uploaded_resume_text = ""  # Dynamically populated via user upload
# types of the interviewers and their corresponding prompts to guide the tone and focus of the questions.

PERSONA_PROMPTS = {
    "Friendly HR": "You are a warm, encouraging HR Manager. Focus heavily on behavioral alignment, teamwork, communication style, and cultural addition. Make your tone welcoming and conversational.",
    "Senior Engineer": "You are a highly pragmatic Senior Staff Engineer. Focus on system design trade-offs, clean architectural patterns, edge cases, and code maintainability. Keep your tone direct and technical.",
    "Google Interviewer": "You are an analytical Technical Interviewer at Google. Ask mathematically or structurally rigorous questions focusing on scale, algorithmic efficiency, optimization, and edge-case data layouts.",
    "Startup Founder": "You are a fast-moving Startup Founder. Focus on speed of execution, ownership mentality, shipping MVPs, and business impact. Your questions are sharp, high-level, and target resourcefulness.",
    "Strict Interviewer": "You are a rigorous, no-nonsense corporate interviewer. Maintain a highly critical, formal tone. Aggressively probe vague statements, challenge incomplete logic, and hold a high bar for evaluation values."
}

@app.route("/")
def home():
    return send_file("index.html")
# //////////////////////////////////////
# uploading of the resume and its processing is handled in a completely isolated manner through the resume_worker module. This ensures that any issues with file parsing do not affect the core interview logic. The extracted text is stored in a global variable for later use during the evaluation phase, where it can be dynamically injected into the prompt based on the interview's progression.


@app.route("/upload_resume", methods=["POST"])
def upload_resume():
    global uploaded_resume_text
    if 'resume' not in request.files:
        return jsonify({"error": "No file chunk found"}), 400
        
    file = request.files['resume']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    # Save temporarily to parse it
    temp_path = os.path.join(".", file.filename)
    file.save(temp_path)
    
    # Extract text using our isolated worker module
    uploaded_resume_text = extract_resume_text(temp_path)
    
    # Cleanup file right away
    if os.path.exists(temp_path):
        os.remove(temp_path)

    return jsonify({
        "message": "Resume uploaded and processed successfully!",
        "preview": uploaded_resume_text[:200] + "..." if uploaded_resume_text else "Empty text extraction"
    })

# //////////////////////////////////////////////////////////
@app.route("/start", methods=["POST"])
def start_interview():
    global session_history, uploaded_resume_text
    session_history = []  # Clear previous chat logs
    # We do NOT clear uploaded_resume_text here so that the file 
    # processed right before clicking "Start" is preserved!
    
    data = request.get_json() or {}
    role = data.get("role", "Software Engineer")
    mode = data.get("mode", "Friendly HR")
    has_resume = data.get("has_resume", False)
    
    if not has_resume:
        uploaded_resume_text = ""

    persona_instruction = PERSONA_PROMPTS.get(mode, PERSONA_PROMPTS["Friendly HR"])
    prompt = f"{persona_instruction}\nConduct an interview for a {role} position. Ask ONLY the first introductory interview question. Do not wrap it in JSON."

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150
        )
        question = response.choices[0].message.content.strip()
    except Exception as e:
        question = f"Welcome. Let's begin the interview for the {role} position. Can you introduce yourself?"

    return jsonify({"question": question})



@app.route("/evaluate", methods=["POST"])
def evaluate():
    global session_history, uploaded_resume_text
    data = request.get_json() or {}
    role = data.get("role")
    mode = data.get("mode", "Friendly HR")
    question = data.get("question")
    answer = data.get("answer")
    follow_up_count = data.get("follow_up_count", 0)

    current_question_index = len(session_history) + 1 
    persona_instruction = PERSONA_PROMPTS.get(mode, PERSONA_PROMPTS["Friendly HR"])

    # Inject Dynamic Resume Instructions based on sequence depth IF text exists
    resume_strategy = ""
    if current_question_index >= 3 and uploaded_resume_text:
        resume_strategy = f"""
CRITICAL COMPLIANCE TARGET: The candidate has completed {current_question_index} questions. 
You are now inside the explicit background verification phase. You MUST read the Candidate Resume Context provided below and target a highly non-static, specific question digging into an explicit framework, project, or role listed on their resume. 

Ensure the question flows naturally within your conversation—do not just read a template. Frame it according to your unique persona traits.

Candidate Resume Context:
{uploaded_resume_text}
"""

    prompt = f"""
{persona_instruction}

You are evaluating a candidate for a {role} position.
Question Asked: {question}
Candidate Answer: {answer}
Current Follow-up Depth on this topic: {follow_up_count}
{resume_strategy}

Tasks:
1. Evaluate the answer objectively through the lens of your persona.
2. Determine the next question. Follow up dynamically if the reply was weak, but pivot to new topics smoothly.
3. If Current Follow-up Depth is 2 or higher, cleanly transition topics.

Output a raw JSON object matching this exact format:
{{
  "score": 7,
  "feedback": "Critique matching your specific persona.",
  "improvement": "Actionable advice.",
  "next_question": "The next conversational question text.",
  "is_follow_up": true/false
}}
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400
        )
        text = response.choices[0].message.content.strip()
        
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            result = json.loads(match.group(0))
        else:
            raise ValueError("Malformed response")
            
    except Exception as e:
        result = {
            "score": 6,
            "feedback": "Response logged.",
            "improvement": "Be more explicit with concrete functional explanations.",
            "next_question": "Can you elaborate further on how you evaluate metrics in production?",
            "is_follow_up": False
        }

    session_history.append({
        "question": question,
        "answer": answer,
        "score": int(result.get("score", 6)),
        "feedback": result.get("feedback"),
        "improvement": result.get("improvement")
    })

    return jsonify({
        "next_question": result.get("next_question"),
        "is_follow_up": result.get("is_follow_up", False)
    })

    #   this is the final report generation endpoint. It aggregates the session history, calculates average scores, and prompts Gemini to provide a holistic evaluation of the candidate's performance across multiple dimensions. The output is structured in a JSON format for easy consumption by the frontend.

@app.route("/report", methods=["GET"])
def report():
    global session_history
    if not session_history:
        return jsonify({"questions_attempted": 0, "average_score": 0, "history": []})

    avg_score = round(sum(item["score"] for item in session_history) / len(session_history), 1)
    
    # 1. Prepare the full interview transcript for holistic AI analysis
    transcript = ""
    for i, item in enumerate(session_history, 1):
        transcript += f"Q{i}: {item['question']}\nA: {item['answer']}\nScore: {item['score']}/10\nFeedback: {item['feedback']}\n---\n"

    # 2. Prompt Gemini to act as a hiring committee and evaluate the overall performance
    prompt = f"""
    You are an Expert Talent Acquisition Director. Analyze the following AI mock interview transcript and generate a comprehensive final assessment report.
    
    Transcript:
    {transcript}
    
    Output MUST be a raw JSON object matching this exact schema:
    {{
        "executive_summary": "A 3-sentence professional summary of the candidate's overall performance, referencing specific patterns in their answers.",
        "strengths": ["Short strength 1", "Short strength 2", "Short strength 3"],
        "weaknesses": ["Short area to improve 1", "Short area to improve 2", "Short area to improve 3"],
        "dimensions": {{
            "Technical Knowledge": <number 1-10>,
            "Communication Skills": <number 1-10>,
            "Problem Solving": <number 1-10>,
            "Confidence": <number 1-10>,
            "Relevance": <number 1-10>
        }},
        "roadmap": ["Actionable step 1", "Actionable step 2", "Actionable step 3"]
    }}
    """

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800
        )
        text = response.choices[0].message.content.strip()
        
        # Safely extract JSON from the response
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            ai_analysis = json.loads(match.group(0))
        else:
            raise ValueError("No JSON found in response")
            
    except Exception as e:
        print(f"Report Generation Error: {e}")
        # Fallback payload if the LLM fails to format correctly
        ai_analysis = {
            "executive_summary": f"The candidate completed {len(session_history)} questions with an average score of {avg_score}/10.",
            "strengths": ["Completed the assessment", "Maintained professionalism"],
            "weaknesses": ["Needs deeper technical specifics", "Expand on edge cases"],
            "dimensions": {
                "Technical Knowledge": avg_score, "Communication Skills": avg_score,
                "Problem Solving": avg_score, "Confidence": avg_score, "Relevance": avg_score
            },
            "roadmap": ["Review core concepts", "Practice more mock interviews"]
        }

    return jsonify({
        "questions_attempted": len(session_history),
        "average_score": avg_score,
        "history": session_history,
        "ai_analysis": ai_analysis
    })
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)