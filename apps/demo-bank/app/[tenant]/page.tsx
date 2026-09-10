import { notFound } from "next/navigation";

const tenants = {
  harbor: {
    institution: "Harbor Credit Union",
    accent: "#0b6bcb",
  },
  summit: {
    institution: "Summit Community Bank",
    accent: "#087b5b",
  },
} as const;

type Tenant = keyof typeof tenants;

export default async function TenantShell({
  params,
}: {
  params: Promise<{ tenant: string }>;
}) {
  const { tenant } = await params;
  if (!(tenant in tenants)) notFound();
  const configuration = tenants[tenant as Tenant];

  return (
    <main className="shell" style={{ "--accent": configuration.accent } as React.CSSProperties}>
      <header className="topbar">
        <div className="brand-mark">N</div>
        <div>
          <p className="eyebrow">Northstar Core Services</p>
          <h1>{configuration.institution}</h1>
        </div>
        <div className="operator">Synthetic training environment</div>
      </header>
      <div className="workspace">
        <nav aria-label="Primary navigation" className="sidebar">
          <strong>Member servicing</strong>
          <a aria-current="page" href={`/${tenant}`}>
            Member search
          </a>
          <span>Account maintenance</span>
          <span>Transaction research</span>
          <span>Administration</span>
        </nav>
        <section className="legacy-panel" aria-label="Member operations application">
          <div className="panel-title">Member Operations</div>
          <iframe
            title="Member operations"
            src={`/${tenant}/member-search`}
            className="operations-frame"
          />
        </section>
      </div>
    </main>
  );
}
