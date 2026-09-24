import { JOBS, JOBS_INTRO } from '../content/landing';
import type { Job } from '../content/types';
import { Preview } from './Previews';

function JobArticle({ job }: { readonly job: Job }) {
  return (
    <article className="job">
      <figure className="jp">
        <img src={job.photo.src} alt={job.photo.alt} loading="lazy" />
        <figcaption>{job.photo.caption}</figcaption>
      </figure>
      <div>
        <h3>{job.title}</h3>
        <p>
          <b>Today:</b> {job.today}
        </p>
        <p>
          <b>With Evidenceline:</b> {job.withIt}
        </p>
        <Preview id={job.preview} />
      </div>
    </article>
  );
}

export function Jobs() {
  return (
    <section className="jobs" id="jobs" aria-labelledby="jobsh">
      <div className="wrap">
        <h2 id="jobsh">{JOBS_INTRO.title}</h2>
        <p className="lead">{JOBS_INTRO.lead}</p>
        {JOBS.map((job) => (
          <JobArticle key={job.title} job={job} />
        ))}
      </div>
    </section>
  );
}
