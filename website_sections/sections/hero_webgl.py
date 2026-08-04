from ..models import SectionSpec

_HERO_WEBGL_JSX = """import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import "./HeroWebGL.css";

function DriftingParticles() {
  const pointsRef = useRef(null);
  const particleCount = 600;
  const positions = useMemo(() => {
    const values = new Float32Array(particleCount * 3);
    for (let i = 0; i < particleCount; i += 1) {
      values[i * 3] = (Math.random() - 0.5) * 12;
      values[i * 3 + 1] = (Math.random() - 0.5) * 8;
      values[i * 3 + 2] = (Math.random() - 0.5) * 6;
    }
    return values;
  }, []);

  useFrame((_state, delta) => {
    if (!pointsRef.current) return;
    pointsRef.current.rotation.y += delta * 0.05;
    pointsRef.current.rotation.x += delta * 0.015;
  });

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={particleCount}
          array={positions}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial size={0.035} color="#8ab4ff" sizeAttenuation transparent opacity={0.85} />
    </points>
  );
}

export default function HeroWebGL() {
  return (
    <section className="hero-webgl" id="hero">
      <div className="hero-webgl__canvas">
        <Canvas camera={{ position: [0, 0, 6], fov: 50 }} dpr={[1, 1.5]}>
          <ambientLight intensity={0.6} />
          <DriftingParticles />
        </Canvas>
      </div>
      <div className="hero-webgl__content">
        <h1>%%CONTENT:headline%%</h1>
        <p>%%CONTENT:subhead%%</p>
        <a className="hero-webgl__cta" href="#highlights">
          %%CONTENT:cta_label%%
        </a>
      </div>
    </section>
  );
}
"""

_HERO_WEBGL_CSS = """.hero-webgl {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: radial-gradient(circle at 50% 20%, #1b2140 0%, #060814 70%);
  color: #f5f7ff;
}

.hero-webgl__canvas {
  position: absolute;
  inset: 0;
}

.hero-webgl__content {
  position: relative;
  z-index: 1;
  max-width: 42rem;
  text-align: center;
  padding: 2rem;
}

.hero-webgl__content h1 {
  font-size: clamp(2.25rem, 5vw, 4rem);
  margin: 0 0 1rem;
  letter-spacing: -0.02em;
}

.hero-webgl__content p {
  font-size: clamp(1rem, 2vw, 1.25rem);
  opacity: 0.82;
  margin: 0 0 2rem;
}

.hero-webgl__cta {
  display: inline-block;
  padding: 0.85rem 2rem;
  border-radius: 999px;
  background: #8ab4ff;
  color: #060814;
  font-weight: 600;
  text-decoration: none;
  transition: transform 0.2s ease;
}

.hero-webgl__cta:hover {
  transform: translateY(-2px);
}
"""

HERO_WEBGL = SectionSpec(
    slug="hero_webgl",
    display_name="WebGL Hero",
    description="Full-viewport hero with an animated Three.js particle backdrop, a headline, a subheadline, and a single call-to-action.",
    when_to_use="Use as the first section of any cinematic/showcase site. Exactly one per page.",
    component_name="HeroWebGL",
    entry_relative_path="sections/HeroWebGL",
    content_schema=(
        ("headline", "Short, high-impact headline (a few words to one sentence)."),
        ("subhead", "One or two supporting sentences beneath the headline."),
        ("cta_label", "Short call-to-action button label, e.g. 'See the work'."),
    ),
    npm_dependencies=("three", "@react-three/fiber"),
    files={
        "sections/HeroWebGL.jsx": _HERO_WEBGL_JSX,
        "sections/HeroWebGL.css": _HERO_WEBGL_CSS,
    },
)
