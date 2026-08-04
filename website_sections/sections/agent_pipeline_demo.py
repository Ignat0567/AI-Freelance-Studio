from ..models import SectionSpec

_AGENT_PIPELINE_DEMO_JSX = """import { useEffect, useRef } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import "./AgentPipelineDemo.css";

gsap.registerPlugin(ScrollTrigger);

const PIPELINE_STAGES = [
  { stage: "Requirements", agent: "Alex", detail: "Clarifies scope and drafts the project brief." },
  { stage: "Planning", agent: "Studio", detail: "Plans the implementation approach." },
  { stage: "Implementation", agent: "Codex", detail: "Writes the code." },
  { stage: "Verification", agent: "BugCatcher", detail: "Runs tests and catches bugs." },
  { stage: "Delivery", agent: "Product Judge", detail: "Reviews quality before delivery." },
];

export default function AgentPipelineDemo() {
  const sectionRef = useRef(null);

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return undefined;
    const items = section.querySelectorAll(".agent-pipeline__stage");
    const animation = gsap.fromTo(
      items,
      { opacity: 0, y: 32 },
      {
        opacity: 1,
        y: 0,
        duration: 0.6,
        stagger: 0.2,
        ease: "power2.out",
        scrollTrigger: {
          trigger: section,
          start: "top 70%",
        },
      }
    );
    return () => {
      if (animation.scrollTrigger) {
        animation.scrollTrigger.kill();
      }
      animation.kill();
    };
  }, []);

  return (
    <section className="agent-pipeline" id="how-it-works" ref={sectionRef}>
      <h2 className="agent-pipeline__heading">%%CONTENT:heading%%</h2>
      <p className="agent-pipeline__intro">%%CONTENT:intro%%</p>
      <ol className="agent-pipeline__stages">
        {PIPELINE_STAGES.map((item) => (
          <li className="agent-pipeline__stage" key={item.stage}>
            <span className="agent-pipeline__stage-agent">{item.agent}</span>
            <span className="agent-pipeline__stage-name">{item.stage}</span>
            <span className="agent-pipeline__stage-detail">{item.detail}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
"""

_AGENT_PIPELINE_DEMO_CSS = """.agent-pipeline {
  padding: 6rem 2rem;
  max-width: 56rem;
  margin: 0 auto;
  color: var(--color-text);
  background: var(--color-background);
}

.agent-pipeline__heading {
  font-family: var(--font-heading, inherit);
}

.agent-pipeline__intro {
  opacity: 0.8;
  margin: 0 0 2.5rem;
  max-width: 40rem;
}

.agent-pipeline__stages {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 1rem;
  border-left: 2px solid var(--color-accent);
}

.agent-pipeline__stage {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 1rem;
  align-items: baseline;
  padding: 0.75rem 0 0.75rem 1.5rem;
  position: relative;
}

.agent-pipeline__stage::before {
  content: "";
  position: absolute;
  left: -0.4375rem;
  top: 1.25rem;
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 999px;
  background: var(--color-accent);
}

.agent-pipeline__stage-agent {
  font-family: var(--font-heading, inherit);
  font-weight: 700;
  color: var(--color-accent);
}

.agent-pipeline__stage-name {
  font-weight: 600;
}

.agent-pipeline__stage-detail {
  grid-column: 1 / -1;
  opacity: 0.75;
  font-size: 0.95rem;
}
"""

AGENT_PIPELINE_DEMO = SectionSpec(
    slug="agent_pipeline_demo",
    display_name="Agent Pipeline Demo",
    description="An animated, illustrative walkthrough of the Studio's real multi-agent pipeline stages (requirements, planning, implementation, verification, delivery), each with its real responsible agent.",
    when_to_use="Use for a 'how it works' section demonstrating the Studio's own generative process. Exactly one per page.",
    component_name="AgentPipelineDemo",
    entry_relative_path="sections/AgentPipelineDemo",
    content_schema=(
        ("heading", "Section heading introducing how the Studio works."),
        ("intro", "One or two sentences introducing the pipeline walkthrough."),
    ),
    npm_dependencies=("gsap",),
    files={
        "sections/AgentPipelineDemo.jsx": _AGENT_PIPELINE_DEMO_JSX,
        "sections/AgentPipelineDemo.css": _AGENT_PIPELINE_DEMO_CSS,
    },
)
