from ..models import SectionSpec

_CLOSING_CTA_JSX = """import { useEffect, useRef } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import "./ClosingCTA.css";

gsap.registerPlugin(ScrollTrigger);

export default function ClosingCTA() {
  const rootRef = useRef(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;
    const animation = gsap.fromTo(
      root,
      { opacity: 0, scale: 0.94 },
      {
        opacity: 1,
        scale: 1,
        duration: 0.9,
        ease: "back.out(1.6)",
        scrollTrigger: {
          trigger: root,
          start: "top 85%",
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
    <section className="closing-cta" id="contact" ref={rootRef}>
      <h2>%%CONTENT:headline%%</h2>
      <p>%%CONTENT:subhead%%</p>
      <a className="closing-cta__button" href="mailto:hello@example.com">
        %%CONTENT:cta_label%%
      </a>
    </section>
  );
}
"""

_CLOSING_CTA_CSS = """.closing-cta {
  padding: 7rem 2rem;
  text-align: center;
  background: #0d1024;
  color: #f5f7ff;
}

.closing-cta h2 {
  font-size: clamp(1.75rem, 4vw, 2.75rem);
  margin: 0 0 1rem;
}

.closing-cta p {
  opacity: 0.8;
  margin: 0 0 2rem;
}

.closing-cta__button {
  display: inline-block;
  padding: 0.9rem 2.25rem;
  border-radius: 999px;
  background: #f5f7ff;
  color: #0d1024;
  font-weight: 600;
  text-decoration: none;
  transition: transform 0.2s ease;
}

.closing-cta__button:hover {
  transform: translateY(-2px);
}
"""

CLOSING_CTA = SectionSpec(
    slug="closing_cta",
    display_name="Closing Call To Action",
    description="A GSAP entrance-animated closing section with a headline, supporting line, and one contact call-to-action.",
    when_to_use="Use as the final section of any cinematic/showcase site. Exactly one per page.",
    component_name="ClosingCTA",
    entry_relative_path="sections/ClosingCTA",
    content_schema=(
        ("headline", "Closing headline inviting the visitor to take the next step."),
        ("subhead", "One supporting sentence."),
        ("cta_label", "Short call-to-action button label, e.g. 'Get in touch'."),
    ),
    npm_dependencies=("gsap",),
    files={
        "sections/ClosingCTA.jsx": _CLOSING_CTA_JSX,
        "sections/ClosingCTA.css": _CLOSING_CTA_CSS,
    },
)
