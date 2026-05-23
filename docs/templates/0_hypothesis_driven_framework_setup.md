# Phase 0: Hypothesis-Driven Development Framework Setup

## Objective
Define the working relationship, core methodology, and fundamental rules for this project. This prompt establishes the initial system context for the AI agent.

## Methodology: The Hypothesis Hierarchy Model
This project adheres to the **Hypothesis Hierarchy Model**, a structured approach prioritizing validated learning over pre-defined implementation. The specifications we generate are not frozen documents; they are a **Living Documentation** of our hypotheses, the evidence we gather, and the subsequent validated learnings.

### The Five Layers of Hypotheses (Order of Operation)
We approach all features and problems by strictly adhering to the following five layers, descending only when the current layer has been sufficiently validated or a clear pivot point is identified.

1.  **Value Hypothesis:** Does this feature create real value? Does it solve a genuine user or business problem?
    * *e.g., "The 'Career Dispatch' AI matcher must reduce the application time by 15% to be viable."*
2.  **Behavior Hypothesis:** How will the user interact with the feature to realize that value?
    * *e.g., "A user will input their resume and job URL, then expect an automated outreach draft."*
3.  **Domain Hypothesis:** What are the underlying business rules and correct domain logic?
    * *e.g., "A 'JobMatcher' domain object must handle multiple resume formats and job descriptions."*
4.  **Interaction Hypothesis:** What specific UI/UX interaction best enables that behavior while adhering to domain rules?
    * *e.g., "A conversational interface for feedback on job matching vs. a form."*
5.  **Implementation Hypothesis:** What technical construction best fulfills the validated interaction and domain requirements while meeting non-functional criteria (latency, cost, scalability, etc.)?
    * *e.g., "Using an asynchronous job queue for LLM calls is necessary to keep the UI responsive."*

## Fundamental Rules
1.  **Strict Hierarchy:** We will never implement a lower layer (e.g., Implementation) without explicitly defining and validating the higher layers (e.g., Value/Behavior).
2.  **Evidence-Based Decisions:** Every decision must be accompanied by its corresponding **Evidence**. Evidence can include personal intuition, peer feedback, competitive analysis, benchmarks, or small-scale test data.
3.  **Falsifiable Specs:** All specifications must be structured as clear, falsifiable **Acceptance Criteria**.
4.  **Learning as Progress:** A "Specification Change" is a direct result of "Validated Learning." We embrace updates based on new evidence.

---
**Acknowledge and confirm your understanding of this hypothesis-first framework and its five layers.**