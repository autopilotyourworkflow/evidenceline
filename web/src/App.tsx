import { useEffect } from 'react';
import { Footer } from './components/Footer';
import { Header } from './components/Header';
import { PAGE_TITLES } from './content/site';
import { installGlossaryListeners } from './lib/glossary';
import { routeFor, useLocation, useScrollOnNavigate, type RouteId } from './lib/router';
import { AccuracyPage } from './pages/Accuracy';
import { LandingPage } from './pages/Landing';
import { NotFoundPage } from './pages/NotFound';
import { SampleSitePage } from './pages/SampleSite';
import { SourcesPage } from './pages/Sources';
import { TidyPage } from './pages/Tidy';

function Page({ route }: { readonly route: RouteId }) {
  switch (route) {
    case 'home':
      return <LandingPage />;
    case 'sample-site':
      return <SampleSitePage />;
    case 'tidy':
      return <TidyPage />;
    case 'sources':
      return <SourcesPage />;
    case 'accuracy':
      return <AccuracyPage />;
    case 'not-found':
      return <NotFoundPage />;
  }
}

export function App() {
  const { pathname, hash } = useLocation();
  const route = routeFor(pathname);

  useEffect(() => installGlossaryListeners(), []);
  useEffect(() => {
    document.title = PAGE_TITLES[route];
  }, [route]);
  useScrollOnNavigate(pathname, hash);

  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header onLanding={route === 'home'} />
      <main id="main">
        <Page route={route} />
      </main>
      <Footer />
    </>
  );
}
