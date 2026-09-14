import InterventionConsole from "./console";

export default function InterventionsPage() {
  return (
    <main>
      <a className="back-link" href="/">
        ← Run and watch
      </a>
      <InterventionConsole />
    </main>
  );
}
