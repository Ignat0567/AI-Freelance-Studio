from ..models import SectionSpec

_SCROLL_REVEAL_JSX = """import { useEffect, useRef } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import "./ScrollRevealSection.css";

gsap.registerPlugin(ScrollTrigger);

export default function ScrollRevealSection() {
  const sectionRef = useRef(null);

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return undefined;
    const items = section.querySelectorAll(".scroll-reveal__item");
    const animation = gsap.fromTo(
      items,
      { opacity: 0, y: 48 },
      {
        opacity: 1,
        y: 0,
        duration: 0.8,
        stagger: 0.15,
        ease: "power3.out",
        scrollTrigger: {
          trigger: section,
          start: "top 75%",
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
    <section className="scroll-reveal" id="highlights" ref={sectionRef}>
      <h2 className="scroll-reveal__item">%%CONTENT:heading%%</h2>
      <p className="scroll-reveal__item scroll-reveal__body">%%CONTENT:body%%</p>
      <ul className="scroll-reveal__list">
        <li className="scroll-reveal__item">%%CONTENT:point_one%%</li>
        <li className="scroll-reveal__item">%%CONTENT:point_two%%</li>
        <li className="scroll-reveal__item">%%CONTENT:point_three%%</li>
      </ul>
    </section>
  );
}
"""

_SCROLL_REVEAL_CSS = """.scroll-reveal {
  padding: 6rem 2rem;
  max-width: 48rem;
  margin: 0 auto;
  color: #1a1d29;
}

.scroll-reveal__body {
  font-size: 1.1rem;
  opacity: 0.8;
  margin-bottom: 2rem;
}

.scroll-reveal__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 1rem;
}

.scroll-reveal__list .scroll-reveal__item {
  padding: 1.25rem 1.5rem;
  border-radius: 0.75rem;
  background: #f2f4fb;
  font-weight: 500;
}
"""

SCROLL_REVEAL_SECTION = SectionSpec(
    slug="scroll_reveal_section",
    display_name="Scroll Reveal Content",
    description="A GSAP ScrollTrigger-driven content block: heading, body copy, and three staggered highlight points that fade/slide in as the user scrolls to them.",
    when_to_use="Use for a features/story/highlights section after the hero. Can appear more than once for multiple content blocks.",
    component_name="ScrollRevealSection",
    entry_relative_path="sections/ScrollRevealSection",
    content_schema=(
        ("heading", "Section heading."),
        ("body", "One to two sentences of supporting copy."),
        ("point_one", "First highlight, a short phrase or sentence."),
        ("point_two", "Second highlight, a short phrase or sentence."),
        ("point_three", "Third highlight, a short phrase or sentence."),
    ),
    npm_dependencies=("gsap",),
    files={
        "sections/ScrollRevealSection.jsx": _SCROLL_REVEAL_JSX,
        "sections/ScrollRevealSection.css": _SCROLL_REVEAL_CSS,
    },
)
