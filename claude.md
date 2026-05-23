# Project Setting: Hypothesis Hierarchy Model

## 1. Vision & Methodology
This project uses the **Hypothesis Hierarchy Model** for software development, which values validated learning over upfront specification. Our specifications are not static documents but a **Living Documentation** of our collective hypotheses, the evidence we gather, and the subsequent validated learnings (Validated Learning).

## 2. Hypothesis Layers (Order of Operation)
We address all features and problems through the strict application of the five layers of hypotheses below. We do not proceed to a lower layer (e.g., Implementation) without explicitly defining and validating the higher layers (e.g., Value/Behavior).

1.  **Value Hypothesis:** Does this feature provide real value? Does it solve a genuine user or business problem?
2.  **Behavior Hypothesis:** How will the user interact with the feature to realize that value?
3.  **Domain Hypothesis:** What are the underlying business rules and correct domain logic?
4.  **Interaction Hypothesis:** What specific UI/UX interaction best enables that behavior while adhering to domain rules?
5.  **Implementation Hypothesis:** What technical construction best fulfills the validated interaction and domain requirements while meeting non-functional criteria?

## 3. Operations & Rules
* **Minimalist Start:** Every new feature discussion begins with the definition of the Value and Behavior hypotheses. The AI agent must not suggest implementation until these are validated.
* **Living Specs:** A "Specification Change" is not a failure; it is a direct result of Validated Learning. All specifications must be structure as falsifiable Acceptance Criteria.
* **Evidence is Mandatory:** All decisions must be supported by evidence ( intuition, peer feedback, competitive analysis, benchmarks, test data).
* **Use of Templates:** The AI agent must use the provided phase-specific templates to structure our work and maintain the Living Documentation.

---
**This file defines the overarching methodology and rules of engagement for all interactions between the user and the AI agent on this project.**