type IntroPageProps = {
  title: string;
  description: string;
  points: string[];
};

export function IntroPage({ title, description, points }: IntroPageProps) {
  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      <section className="panel intro-panel">
        <div className="list">
          {points.map((point) => (
            <div className="list-item" key={point}>
              {point}
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}
