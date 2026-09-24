import { About } from '../landing/About';
import { FlowCard } from '../landing/FlowCard';
import { Hero } from '../landing/Hero';
import { Jobs } from '../landing/Jobs';
import { TrustBand } from '../landing/TrustBand';
import { TryIt } from '../landing/TryIt';
import { Walkthrough } from '../landing/Walkthrough';

/** The story: what it is, how it works, the three jobs, the walkthrough, why trust it, try it, why I built it. */
export function LandingPage() {
  return (
    <>
      <Hero />
      <FlowCard />
      <Jobs />
      <Walkthrough />
      <TrustBand />
      <TryIt />
      <About />
    </>
  );
}
