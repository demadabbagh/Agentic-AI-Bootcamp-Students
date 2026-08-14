# Module 2 - Phase 1 Mini-Project: Study Guide Agent

## Project Description

This project implements the same Study Guide Agent using two frameworks: LangGraph and CrewAI.

The Study Guide Agent takes a topic and produces:

1. A plain-language explanation.
2. A practical example and a common misconception.
3. A three-question quiz with an answer key.

## Setup

Create and activate a virtual environment, then install the required dependencies.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install langgraph langchain-openai crewai python-dotenv
```

Create a `.env` file containing your OpenRouter API key:

```text
OPENROUTER_API_KEY=your_key_here
```

## Run LangGraph

```powershell
python Module-02/starter-code/study_guide_langgraph.py "Model Context Protocol"
```

## Run CrewAI

```powershell
python Module-02/starter-code/study_guide_crewai.py "Model Context Protocol"
```

## Tested Topic

Model Context Protocol

## Observations

1. **LangGraph made the control flow explicit.**  
   I had to create the three nodes and manually connect them with edges. The state also made it clear what information was passed between the tasks.

2. **CrewAI automated more of the orchestration.**  
   I used one Agent and three Tasks with `Process.sequential`. The `context` parameter allowed later tasks to use the outputs of earlier tasks without manually creating graph edges.

3. **For this three-task pipeline, I would choose CrewAI.**  
   It required less orchestration code and the sequential workflow was simple to build. LangGraph would be useful when I need more explicit control over a more complex workflow.